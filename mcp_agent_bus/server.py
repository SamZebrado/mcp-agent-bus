from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from .bus import AgentBus, BusError


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


TOOLS: dict[str, dict[str, Any]] = {
    "register_agent": schema(
        {"agent_name": {"type": "string"}, "role": {"type": "string"}},
        ["agent_name"],
    ),
    "send_task": schema(
        {
            "to": {"type": "string"},
            "body": {"type": "string"},
            "acceptance_criteria": {},
            "priority": {"type": "integer"},
            "timeout_s": {"type": "integer"},
            "from_agent": {"type": "string"},
        },
        ["to", "body"],
    ),
    "wait_for_task": schema(
        {
            "agent_name": {"type": "string"},
            "max_wait_s": {"type": "integer"},
            "lease_s": {"type": "integer"},
        },
        ["agent_name"],
    ),
    "poll_for_task": schema(
        {
            "agent_name": {"type": "string"},
            "lease_s": {"type": "integer"},
        },
        ["agent_name"],
    ),
    "claim_task": schema(
        {"task_id": {"type": "string"}, "agent_name": {"type": "string"}, "lease_s": {"type": "integer"}},
        ["task_id", "agent_name"],
    ),
    "append_progress": schema(
        {"task_id": {"type": "string"}, "agent_name": {"type": "string"}, "message": {"type": "string"}, "evidence": {}},
        ["task_id", "agent_name", "message"],
    ),
    "finish_task": schema(
        {
            "task_id": {"type": "string"},
            "agent_name": {"type": "string"},
            "status": {"type": "string", "enum": ["blocked", "done", "failed", "rejected", "cancelled", "expired"]},
            "summary": {"type": "string"},
            "changed_files": {},
            "evidence": {},
            "error_message": {"type": "string"},
        },
        ["task_id", "agent_name", "status", "summary"],
    ),
    "wait_for_result": schema(
        {"task_id": {"type": "string"}, "max_wait_s": {"type": "integer"}},
        ["task_id"],
    ),
    "poll_for_result": schema(
        {"task_id": {"type": "string"}},
        ["task_id"],
    ),
    "get_task": schema({"task_id": {"type": "string"}}, ["task_id"]),
    "list_tasks": schema({"filter": {"type": "object"}}, []),
    "cleanup_event_log": schema(
        {
            "keep_last_lines": {"type": "integer"},
            "archive": {"type": "boolean"},
            "min_bytes": {"type": "integer"},
        },
        [],
    ),
}


class McpServer:
    def __init__(self, data_dir: Path | None = None):
        self.bus = AgentBus(data_dir)

    def close(self) -> None:
        self.bus.close()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-agent-bus", "version": "0.1.0"},
                }
                return {"jsonrpc": "2.0", "id": req_id, "result": result}
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                tools = [
                    {
                        "name": name,
                        "description": tool_description(name),
                        "inputSchema": input_schema,
                    }
                    for name, input_schema in TOOLS.items()
                ]
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
            if method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                result = self.call_tool(name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}],
                        "isError": False,
                    },
                }
            return self.error(req_id, -32601, f"method not found: {method}")
        except Exception as exc:
            message = str(exc)
            if not isinstance(exc, BusError):
                message = f"{message}\n{traceback.format_exc()}"
            return self.error(req_id, -32000, message)

    def error(self, req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in TOOLS:
            raise BusError(f"unknown tool: {name}")
        func: Callable[..., Any] = getattr(self.bus, name)
        return func(**arguments)


def tool_description(name: str) -> str:
    descriptions = {
        "register_agent": "Register or refresh a local agent identity.",
        "send_task": "Create a delegated task for another agent.",
        "wait_for_task": "Bounded wait for the next task for an agent, then claim it with a lease.",
        "poll_for_task": "Non-blocking check for the next task for an agent; claim it if available.",
        "claim_task": "Claim a specific new or expired task.",
        "append_progress": "Append auditable progress and mark claimed tasks running.",
        "finish_task": "Finish or block a claimed task with summary and evidence.",
        "wait_for_result": "Bounded wait for a task to reach a terminal result.",
        "poll_for_result": "Non-blocking check for a task's result; return immediately.",
        "get_task": "Read a task including progress entries.",
        "list_tasks": "List tasks with optional status/to/limit filter.",
        "cleanup_event_log": "Archive old event-log lines and keep only the newest tail in events.jsonl.",
    }
    return descriptions[name]


def main() -> int:
    server = McpServer()
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            response = server.handle(json.loads(line))
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
