"""Small concurrent stdio MCP facade; SDK and child launch remain lazy/opt-in."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from .config import BridgeError
from .runner import Runner

PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
PROPERTIES = {"profile": {"type": "string", "maxLength": 48}, "text": {"type": "string", "minLength": 1, "maxLength": 24000}}


def schema(name, description, properties, required, read_only=False):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False, "properties": properties, "required": required},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": name == "acp_prompt", "idempotentHint": read_only, "openWorldHint": not read_only}}


TOOLS = [
    schema("acp_preview", "Offline approval fingerprint for exact text and static operator profile. No process, SDK, files or API call. Preview is not consent.", PROPERTIES, ["profile", "text"], True),
    schema("acp_prompt", "Run one explicitly human-approved text prompt through a preconfigured external ACP v1 agent. Can incur provider costs and agent side effects; not an OS sandbox. No automatic replay. Requires trusted host approval UI.",
           {**PROPERTIES, "user_approved_agent_run": {"type": "boolean", "const": True},
            "approved_request_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
           ["profile", "text", "user_approved_agent_run", "approved_request_sha256"]),
    schema("acp_status", "Get this bridge's active run ID/state; never includes prompt history or credentials.", {}, [], True),
    schema("acp_cancel", "Request cancellation of an active run. This does NOT confirm provider work stopped or remove possible charges.",
           {"run_id": {"type": "string", "maxLength": 32}}, ["run_id"]),
]


def reject_constant(_):
    raise ValueError()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


class Server:
    def __init__(self, runner, output):
        self.runner = runner
        self.output = output
        self.write_lock = asyncio.Lock()
        self.tasks = {}
        self.cancelled = set()
        self.prompt_request = None
        self.closing = False

    async def send(self, value):
        data = (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
        async with self.write_lock:
            self.output.write(data)
            self.output.flush()

    async def error(self, ident, code, message):
        await self.send({"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}})

    async def result(self, ident, value, tool=False, is_error=False):
        if ident in self.cancelled:
            return
        if tool:
            payload = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True) if isinstance(value, dict) else value}], "isError": is_error}
            if isinstance(value, dict) and not is_error:
                payload["structuredContent"] = value
        else:
            payload = value
        await self.send({"jsonrpc": "2.0", "id": ident, "result": payload})

    async def handle(self, message):
        ident = message.get("id")
        if ident in self.cancelled:
            return
        method = message["method"]
        params = message.get("params", {})
        if not isinstance(params, dict):
            await self.error(ident, -32602, "Invalid params")
            return
        if method == "initialize":
            requested = params.get("protocolVersion")
            await self.result(ident, {"protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[-1],
                "serverInfo": {"name": "agent-bus-acp", "version": "0.1.0.dev1"}, "capabilities": {"tools": {}},
                "instructions": "Preview exact text/profile and obtain explicit human approval. Approval booleans/hashes are caller assertions, not human signatures. ACP is not an OS sandbox or a provider billing limit. Never auto-retry unknown results."})
        elif method == "ping":
            await self.result(ident, {})
        elif method == "tools/list":
            await self.result(ident, {"tools": TOOLS})
        elif method == "tools/call":
            try:
                name = params.get("name")
                tool = next((x for x in TOOLS if x["name"] == name), None)
                args = params.get("arguments", {})
                if tool is None or not isinstance(args, dict) or set(args) - set(tool["inputSchema"]["properties"]) or set(tool["inputSchema"]["required"]) - set(args):
                    raise BridgeError("INVALID_TOOL_ARGUMENTS")
                if name == "acp_preview":
                    value = self.runner.preview(**args)
                elif name == "acp_status":
                    value = self.runner.status()
                elif name == "acp_cancel":
                    if not isinstance(args["run_id"], str) or len(args["run_id"]) != 32:
                        raise BridgeError("INVALID_RUN_ID")
                    value = await self.runner.cancel(**args)
                else:
                    if ident in self.cancelled:
                        return
                    if self.closing:
                        raise BridgeError("SERVER_CLOSING; agent not started")
                    if self.prompt_request is not None:
                        raise BridgeError("BUSY; requests are not queued")
                    self.prompt_request = ident
                    meta = params.get("_meta", {})
                    token = meta.get("progressToken") if isinstance(meta, dict) else None
                    if type(token) not in (int, str) or (isinstance(token, str) and len(token) > 128):
                        token = None
                    last = 0.0
                    last_count = -1
                    async def progress(count):
                        nonlocal last, last_count
                        now = time.monotonic()
                        if token is not None and ident not in self.cancelled and self.prompt_request == ident and count > last_count and now - last >= .1:
                            last = now
                            last_count = count
                            await self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                                "progressToken": token, "progress": count, "message": "ACP output received (bytes): " + str(count)}})
                    try:
                        if ident in self.cancelled or self.closing:
                            return
                        value = await self.runner.run(**args, progress=progress)
                    finally:
                        self.prompt_request = None
                await self.result(ident, value, tool=True)
            except BridgeError as exc:
                await self.result(ident, str(exc), tool=True, is_error=True)
            except Exception:
                await self.result(ident, "BRIDGE_INTERNAL_ERROR; outcome may be unknown; no automatic retry", tool=True, is_error=True)
        else:
            await self.error(ident, -32601, "Method not found")

    async def receive(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            await self.error(None, -32600, "Invalid Request")
            return
        if "id" not in message:
            if message["method"] == "notifications/cancelled":
                params = message.get("params", {})
                if isinstance(params, dict):
                    ident = params.get("requestId")
                    if type(ident) in (int, str) and ident in self.tasks:
                        self.cancelled.add(ident)
                        if self.prompt_request == ident and self.runner.active:
                            try:
                                await self.runner.cancel()
                            except BridgeError:
                                pass
            return
        ident = message["id"]
        if type(ident) not in (int, str) or (isinstance(ident, str) and len(ident) > 128):
            await self.error(None, -32600, "Invalid Request")
            return
        if ident in self.tasks:
            await self.error(ident, -32600, "Duplicate request ID")
            return
        if len(self.tasks) >= 16:
            await self.error(ident, -32000, "Concurrency limit")
            return
        self.cancelled.discard(ident)
        task = asyncio.create_task(self.handle(message))
        self.tasks[ident] = task
        def finished(completed):
            self.tasks.pop(ident, None)
            self.cancelled.discard(ident)
            if not completed.cancelled():
                completed.exception()  # Consume without exposing an exception payload.
        task.add_done_callback(finished)

    async def serve(self, source):
        try:
            while True:
                line = await asyncio.to_thread(source.readline, 524289)
                if not line:
                    break
                if len(line) > 524288:
                    await self.error(None, -32600, "Frame limit; connection closing")
                    break
                try:
                    message = json.loads(line.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_object)
                except (UnicodeError, ValueError, RecursionError):
                    await self.error(None, -32700, "Parse error")
                    continue
                await self.receive(message)
        finally:
            self.closing = True
            # An input EOF does not constitute provider cancellation confirmation.
            if self.runner.active:
                try:
                    await self.runner.cancel()
                except Exception:
                    pass
            if self.tasks:
                _, pending = await asyncio.wait(list(self.tasks.values()), timeout=5)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)


def serve(runner):
    asyncio.run(Server(runner, sys.stdout.buffer).serve(sys.stdin.buffer))
