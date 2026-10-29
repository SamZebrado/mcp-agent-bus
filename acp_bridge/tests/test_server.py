"""MCP facade regressions using only FakeRunner and in-memory stdio."""
import asyncio
import builtins
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from agent_bus_acp.cli import main
from agent_bus_acp.server import Server, TOOLS


ARGS = {"profile": "fake", "text": "offline fixture", "user_approved_agent_run": True,
        "approved_request_sha256": "0" * 64}


def call(name="acp_prompt", arguments=None, ident=1, meta=None):
    params = {"name": name, "arguments": dict(ARGS) if arguments is None else arguments}
    if meta is not None:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": params}


def cancel(ident=1):
    return {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": ident}}


def frames(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


class FakeRunner:
    """Counts boundary calls; deliberately contains no SDK or process code."""
    def __init__(self):
        self.active = None
        self.runs = []
        self.previews = []
        self.cancels = []
        self.status_calls = 0
        self.progress_counts = []
        self.saved_progress = None
        self.block = False
        self.failure = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def preview(self, **kwargs):
        self.previews.append(kwargs)
        return {"profile": "fake", "request_sha256": "0" * 64, "agent_process_enabled": False}

    def status(self):
        self.status_calls += 1
        return {"state": "idle", "enabled": False}

    async def run(self, *, progress=None, **kwargs):
        self.runs.append(kwargs)
        self.active = {"id": "a" * 32}
        self.saved_progress = progress
        self.started.set()
        try:
            if self.failure:
                raise RuntimeError(self.failure)
            for count in self.progress_counts:
                if progress is not None:
                    await progress(count)
            if self.block:
                await self.release.wait()
            return {"status": "completed", "text": "fake result"}
        finally:
            self.active = None

    async def cancel(self, **kwargs):
        self.cancels.append(kwargs)
        self.release.set()
        return {"cancel_requested": True, "cancel_confirmed": False}


class ServerTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        runner, output = FakeRunner(), io.BytesIO()
        return Server(runner, output), runner, output

    async def settle(self, server):
        if server.tasks:
            await asyncio.gather(*list(server.tasks.values()))
        await asyncio.sleep(0)

    async def test_prestart_cancel_does_not_run_or_reply(self):
        server, runner, output = self.make()
        await server.receive(call())
        # No intervening scheduling point: handle has not begun yet.
        await server.receive(cancel())
        await self.settle(server)
        self.assertEqual(runner.runs, [])
        self.assertEqual(runner.cancels, [])
        self.assertEqual(frames(output), [])

    async def test_eof_queued_prompt_blocked_but_readonly_responses_allowed(self):
        server, runner, output = self.make()
        messages = [
            {"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            call("acp_preview", {"profile": "fake", "text": "fixture"}, ident=11),
            call(ident=12),
        ]
        source = io.BytesIO(b"".join(json.dumps(x).encode() + b"\n" for x in messages))
        async def immediate(function, *args):
            return function(*args)
        # Deterministically queue handles before EOF, without real threads.
        with patch("agent_bus_acp.server.asyncio.to_thread", new=immediate):
            await server.serve(source)
        replies = {x["id"]: x for x in frames(output) if "id" in x}
        self.assertEqual(runner.runs, [])
        self.assertEqual(len(runner.previews), 1)
        self.assertIn("result", replies[10])
        self.assertFalse(replies[11]["result"]["isError"])
        self.assertTrue(replies[12]["result"]["isError"])

    async def test_active_cancel_forwarded_and_result_suppressed(self):
        server, runner, output = self.make()
        runner.block = True
        await server.receive(call())
        await asyncio.wait_for(runner.started.wait(), 1)
        await server.receive(cancel())
        await self.settle(server)
        self.assertEqual(len(runner.runs), 1)
        self.assertEqual(len(runner.cancels), 1)
        self.assertEqual(frames(output), [])

    async def test_unknown_cancel_has_no_effect(self):
        server, runner, output = self.make()
        await server.receive(cancel(999))
        self.assertEqual(runner.runs, [])
        self.assertEqual(runner.cancels, [])
        self.assertEqual(frames(output), [])

    async def test_preview_status_and_discovery_do_not_load_sdk_or_spawn(self):
        server, runner, output = self.make()
        real_import = builtins.__import__
        def guard(name, *args, **kwargs):
            if name == "acp" or name.startswith("acp."):
                raise AssertionError("SDK import not permitted for read-only tools")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guard), \
                patch("agent_bus_acp.runner.load_sdk", side_effect=AssertionError("no SDK")) as sdk, \
                patch("asyncio.create_subprocess_exec", side_effect=AssertionError("no process")) as spawn, \
                patch("agent_bus_acp.ledger.Ledger.reserve", side_effect=AssertionError("no state")) as reserve:
            await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            await server.handle(call("acp_preview", {"profile": "fake", "text": "fixture"}, ident=2))
            await server.handle(call("acp_status", {}, ident=3))
            sdk.assert_not_called()
            spawn.assert_not_called()
            reserve.assert_not_called()
        self.assertEqual(len(frames(output)), 3)
        self.assertEqual(runner.runs, [])
        self.assertEqual(len(runner.previews), 1)
        self.assertEqual(runner.status_calls, 1)

    async def test_tool_hints_do_not_misrepresent_external_agent(self):
        tools = {x["name"]: x for x in TOOLS}
        self.assertTrue(tools["acp_prompt"]["annotations"]["destructiveHint"])
        self.assertFalse(tools["acp_prompt"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["acp_prompt"]["annotations"]["openWorldHint"])
        self.assertFalse(tools["acp_prompt"]["annotations"]["idempotentHint"])
        for name in ("acp_preview", "acp_status"):
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[name]["annotations"]["destructiveHint"])

    async def test_extra_execution_arguments_rejected_before_runner(self):
        for key in ("command", "argv", "args", "env", "cwd", "mcpServers", "max_calls", "profile_file"):
            server, runner, output = self.make()
            await server.handle(call(arguments={**ARGS, key: "private-argument-marker"}))
            self.assertEqual(runner.runs, [])
            self.assertTrue(frames(output)[0]["result"]["isError"])
            self.assertNotIn(b"private-argument-marker", output.getvalue())

    async def test_missing_required_arguments_rejected(self):
        server, runner, output = self.make()
        await server.handle(call(arguments={"profile": "fake", "text": "fixture"}))
        self.assertEqual(runner.runs, [])
        self.assertTrue(frames(output)[0]["result"]["isError"])

    async def test_progress_absent_without_requested_token(self):
        server, runner, output = self.make()
        runner.progress_counts = [3, 6]
        with patch("agent_bus_acp.server.time.monotonic", side_effect=[100.0, 101.0]):
            await server.handle(call())
        self.assertFalse(any(x.get("method") == "notifications/progress" for x in frames(output)))

    async def test_progress_uses_only_valid_requested_token(self):
        for token in ("chosen-token", 0):
            server, runner, output = self.make()
            runner.progress_counts = [3]
            with patch("agent_bus_acp.server.time.monotonic", return_value=100.0):
                await server.handle(call(meta={"progressToken": token}))
            updates = [x for x in frames(output) if x.get("method") == "notifications/progress"]
            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0]["params"]["progressToken"], token)
            self.assertEqual(updates[0]["params"]["progress"], 3)

    async def test_invalid_progress_tokens_do_not_create_notifications(self):
        for token in (True, None, [], {}, "x" * 129, 1.5):
            server, runner, output = self.make()
            runner.progress_counts = [3]
            with patch("agent_bus_acp.server.time.monotonic", return_value=100.0):
                await server.handle(call(meta={"progressToken": token}))
            self.assertFalse(any(x.get("method") == "notifications/progress" for x in frames(output)))

    async def test_progress_strictly_increases_and_stops_after_completion(self):
        server, runner, output = self.make()
        runner.progress_counts = [3, 3, 2, 5]
        with patch("agent_bus_acp.server.time.monotonic", side_effect=[100.0, 101.0, 102.0, 103.0, 104.0]):
            await server.handle(call(meta={"progressToken": "chosen"}))
            await runner.saved_progress(8)
        updates = [x["params"]["progress"] for x in frames(output) if x.get("method") == "notifications/progress"]
        self.assertEqual(updates, [3, 5])

    async def test_runtime_exception_body_not_echoed(self):
        server, runner, output = self.make()
        runner.failure = "private upstream fixture marker"
        await server.handle(call())
        self.assertTrue(frames(output)[0]["result"]["isError"])
        self.assertNotIn(b"private upstream fixture marker", output.getvalue())


class CliReadOnlyTests(unittest.TestCase):
    def test_help_and_capabilities_do_not_import_sdk_start_process_or_write_state(self):
        real_import = builtins.__import__
        def guard(name, *args, **kwargs):
            if name == "acp" or name.startswith("acp."):
                raise AssertionError("SDK must remain lazy")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guard), \
                patch("asyncio.create_subprocess_exec", side_effect=AssertionError("no process")) as spawn, \
                patch("agent_bus_acp.runner.Runner.__init__", side_effect=AssertionError("no runtime")) as runtime, \
                patch("agent_bus_acp.ledger.Ledger.reserve", side_effect=AssertionError("no state")) as reserve:
            help_output, error_output = io.StringIO(), io.StringIO()
            with redirect_stdout(help_output), redirect_stderr(error_output):
                with self.assertRaises(SystemExit) as exit_info:
                    main(["--help"])
            self.assertEqual(exit_info.exception.code, 0)
            self.assertIn("capabilities", help_output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["capabilities"]), 0)
            capabilities = json.loads(output.getvalue())
            self.assertFalse(capabilities["default_process_enabled"])
            self.assertFalse(capabilities["provider_cost_limit"])
            self.assertFalse(capabilities["os_sandbox"])
            spawn.assert_not_called()
            runtime.assert_not_called()
            reserve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
