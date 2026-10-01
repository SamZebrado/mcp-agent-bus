from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest


class McpStdioTests(unittest.TestCase):
    def test_tools_list_and_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["MCP_AGENT_BUS_DATA_DIR"] = tmp
            proc = subprocess.Popen(
                [sys.executable, "-m", "mcp_agent_bus.server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            assert proc.stdin is not None
            assert proc.stdout is not None
            try:
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
                proc.stdin.flush()
                init = json.loads(proc.stdout.readline())
                self.assertEqual(init["result"]["serverInfo"]["name"], "mcp-agent-bus")
                self.assertIn("bus_sync", init["result"]["instructions"])

                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
                proc.stdin.flush()
                listed = json.loads(proc.stdout.readline())
                names = {tool["name"] for tool in listed["result"]["tools"]}
                self.assertIn("send_task", names)
                self.assertIn("wait_for_result", names)
                self.assertIn("bus_sync", names)
                self.assertIn("codex_bus_sync", names)
                self.assertIn("doctor", names)
                self.assertIn("cancel_task", names)
                self.assertIn("accept_task_result", names)

                call = {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "register_agent", "arguments": {"agent_name": "worker"}},
                }
                proc.stdin.write(json.dumps(call) + "\n")
                proc.stdin.flush()
                result = json.loads(proc.stdout.readline())
                payload = json.loads(result["result"]["content"][0]["text"])
                self.assertEqual(payload["agent_name"], "worker")

                sync = {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "codex_bus_sync",
                        "arguments": {
                            "agent_name": "planner",
                            "send": [{
                                "to": "worker",
                                "body": "compact task",
                                "client_request_id": "compact-1",
                            }],
                        },
                    },
                }
                proc.stdin.write(json.dumps(sync) + "\n")
                proc.stdin.flush()
                sync_result = json.loads(proc.stdout.readline())
                sync_payload = json.loads(sync_result["result"]["content"][0]["text"])
                self.assertTrue(sync_payload["compact"])
                self.assertEqual(sync_payload["send"][0]["status"], "new")

                doctor = {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "doctor", "arguments": {}},
                }
                proc.stdin.write(json.dumps(doctor) + "\n")
                proc.stdin.flush()
                doctor_result = json.loads(proc.stdout.readline())
                doctor_payload = json.loads(doctor_result["result"]["content"][0]["text"])
                self.assertEqual(doctor_payload["status"], "PASS")
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                if proc.stdin:
                    proc.stdin.close()
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
