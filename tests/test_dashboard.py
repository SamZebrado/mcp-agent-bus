from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_agent_bus.bus import AgentBus
from mcp_agent_bus.dashboard import DashboardHandler
from mcp_agent_bus.dashboard_data import DashboardStore, read_events


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.bus = AgentBus(self.data_dir)

    def tearDown(self) -> None:
        self.bus.close()
        self.tmp.cleanup()

    def test_dashboard_module_imports(self) -> None:
        self.assertIsNotNone(DashboardHandler)

    def test_overview_reads_agents_tasks_and_events(self) -> None:
        self.bus.register_agent("planner", "planner")
        self.bus.register_agent("worker", "worker")
        task = self.bus.send_task("worker", "read-only dashboard test", from_agent="planner", priority=7)
        self.bus.claim_task(task["task_id"], "worker", lease_s=30)
        self.bus.append_progress(task["task_id"], "worker", "observed progress")
        self.bus.finish_task(task["task_id"], "worker", "done", "observed result", changed_files=["README.md"])

        before = (self.data_dir / "events.jsonl").read_text(encoding="utf-8")
        overview = DashboardStore(self.data_dir).overview()
        after = (self.data_dir / "events.jsonl").read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(overview["agents"]), 2)
        self.assertEqual(len(overview["tasks"]), 1)
        self.assertEqual(overview["tasks"][0]["status"], "done")
        self.assertGreaterEqual(len(overview["events"]), 5)

    def test_task_detail_reads_progress_and_task_events(self) -> None:
        task = self.bus.send_task("worker", "detail test", acceptance_criteria=["show progress"])
        self.bus.claim_task(task["task_id"], "worker", lease_s=30)
        self.bus.append_progress(task["task_id"], "worker", "step one", evidence={"ok": True})

        detail = DashboardStore(self.data_dir).task_detail(task["task_id"])

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["task"]["task_id"], task["task_id"])
        self.assertEqual(detail["task"]["acceptance_criteria"], ["show progress"])
        self.assertEqual(detail["task"]["progress"][0]["message"], "step one")
        self.assertTrue(all(event["task_id"] == task["task_id"] for event in detail["events"]))

    def test_missing_database_is_empty_overview(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            overview = DashboardStore(Path(empty)).overview()
        self.assertFalse(overview["db_exists"])
        self.assertEqual(overview["agents"], [])
        self.assertEqual(overview["tasks"], [])

    def test_read_events_handles_invalid_jsonl(self) -> None:
        path = self.data_dir / "events.jsonl"
        path.write_text('{"event_type":"ok","ts":1}\nnot json\n', encoding="utf-8")
        events = read_events(self.data_dir)
        self.assertIn("invalid_jsonl", {event["event_type"] for event in events})


if __name__ == "__main__":
    unittest.main()
