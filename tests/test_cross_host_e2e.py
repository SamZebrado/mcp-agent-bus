from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


class FakeMcpHost:
    """Tiny MCP stdio client used to simulate an independent AI host."""

    def __init__(self, data_dir: str, host_name: str) -> None:
        env = os.environ.copy()
        env["MCP_AGENT_BUS_DATA_DIR"] = data_dir
        self.host_name = host_name
        self._next_id = 1
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_agent_bus.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise AssertionError(f"{self.host_name} server exited early: {stderr}")
        response = json.loads(line)
        if "error" in response:
            raise AssertionError(f"{self.host_name} MCP error: {response['error']}")
        return response["result"]

    def tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()


class CrossHostE2ETests(unittest.TestCase):
    def test_two_independent_fake_hosts_complete_reviewed_handoff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mcp-agent-bus-cross-host-") as tmp:
            planner = FakeMcpHost(tmp, "fake-host-a")
            worker = FakeMcpHost(tmp, "fake-host-b")
            try:
                planner_init = planner.request("initialize")
                worker_init = worker.request("initialize")
                self.assertEqual(planner_init["serverInfo"]["name"], "mcp-agent-bus")
                self.assertEqual(worker_init["serverInfo"]["name"], "mcp-agent-bus")

                planner_tools = planner.request("tools/list")
                worker_tools = worker.request("tools/list")
                planner_names = {tool["name"] for tool in planner_tools["tools"]}
                worker_names = {tool["name"] for tool in worker_tools["tools"]}
                self.assertIn("bus_sync", planner_names)
                self.assertIn("bus_sync", worker_names)
                self.assertIn("codex_bus_sync", planner_names)  # compatibility alias

                planner.tool("register_agent", {"agent_name": "planner-neutral", "role": "planner"})
                worker.tool("register_agent", {"agent_name": "worker-neutral", "role": "worker"})

                sent = planner.tool(
                    "send_task",
                    {
                        "to": "worker-neutral",
                        "from_agent": "planner-neutral",
                        "body": "Return a deterministic fake-host handoff result.",
                        "acceptance_criteria": {
                            "summary": "present",
                            "evidence": {"transport": "stdio", "fake_host": True},
                        },
                        "client_request_id": "cross-host-e2e-1",
                    },
                )
                task_id = sent["task_id"]

                # Ambiguous-send retry: same sender/key/payload must resolve to the same task.
                retried = planner.tool(
                    "send_task",
                    {
                        "to": "worker-neutral",
                        "from_agent": "planner-neutral",
                        "body": "Return a deterministic fake-host handoff result.",
                        "acceptance_criteria": {
                            "summary": "present",
                            "evidence": {"transport": "stdio", "fake_host": True},
                        },
                        "client_request_id": "cross-host-e2e-1",
                    },
                )
                self.assertEqual(retried["task_id"], task_id)

                claimed = worker.tool(
                    "poll_for_task",
                    {"agent_name": "worker-neutral", "lease_s": 30},
                )
                self.assertEqual(claimed["status"], "ok")
                self.assertEqual(claimed["task"]["task_id"], task_id)
                self.assertEqual(claimed["task"]["claimed_by"], "worker-neutral")

                worker.tool(
                    "append_progress",
                    {
                        "task_id": task_id,
                        "agent_name": "worker-neutral",
                        "message": "Fake host received the task over MCP stdio.",
                        "evidence": {"transport": "stdio"},
                    },
                )
                worker.tool(
                    "finish_task",
                    {
                        "task_id": task_id,
                        "agent_name": "worker-neutral",
                        "status": "done",
                        "summary": "Cross-host fake-agent handoff completed.",
                        "evidence": {
                            "transport": "stdio",
                            "fake_host": True,
                            "worker_host": "fake-host-b",
                        },
                    },
                )

                result = planner.tool("poll_for_result", {"task_id": task_id})
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["task"]["status"], "done")
                self.assertEqual(result["task"]["evidence"]["fake_host"], True)

                reviewed = planner.tool(
                    "accept_task_result",
                    {
                        "task_id": task_id,
                        "agent_name": "planner-neutral",
                        "note": "Fake-host E2E evidence verified.",
                    },
                )
                self.assertEqual(reviewed["result_review"], "accepted")

                doctor = planner.tool("doctor")
                self.assertEqual(doctor["status"], "PASS")

                events = [
                    json.loads(line)
                    for line in (Path(tmp) / "events.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                event_types = [event["event_type"] for event in events]
                self.assertEqual(event_types.count("task_sent"), 1)
                self.assertIn("task_claimed", event_types)
                self.assertIn("task_finished", event_types)
                self.assertIn("task_result_reviewed", event_types)
            finally:
                worker.close()
                planner.close()


if __name__ == "__main__":
    unittest.main()
