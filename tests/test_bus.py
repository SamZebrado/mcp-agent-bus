from __future__ import annotations

import tempfile
import time
import unittest
import json
from pathlib import Path

from mcp_agent_bus.bus import AgentBus, BusError


class BusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = AgentBus(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.bus.close()
        self.tmp.cleanup()

    def test_two_agent_flow(self) -> None:
        self.bus.register_agent("planner")
        self.bus.register_agent("worker")
        task = self.bus.send_task("worker", "do it", from_agent="planner", priority=5)

        claimed = self.bus.wait_for_task("worker", max_wait_s=1, lease_s=10)
        self.assertEqual(claimed["status"], "ok")
        self.assertEqual(claimed["task"]["task_id"], task["task_id"])

        self.bus.append_progress(task["task_id"], "worker", "running")
        result = self.bus.finish_task(task["task_id"], "worker", "done", "complete")
        self.assertEqual(result["status"], "done")

        waited = self.bus.wait_for_result(task["task_id"], max_wait_s=1)
        self.assertEqual(waited["status"], "ok")
        self.assertEqual(waited["task"]["status"], "done")

    def test_bounded_timeout_wait_for_task(self) -> None:
        start = time.time()
        result = self.bus.wait_for_task("missing", max_wait_s=1)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.time() - start, 2)

    def test_bounded_timeout_wait_for_result(self) -> None:
        task = self.bus.send_task("worker", "test")
        start = time.time()
        result = self.bus.wait_for_result(task["task_id"], max_wait_s=1)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.time() - start, 2)

    def test_lease_expiry_makes_task_claimable_again(self) -> None:
        task = self.bus.send_task("worker", "lease test")
        self.bus.claim_task(task["task_id"], "worker", lease_s=1)
        time.sleep(1.2)

        claimed = self.bus.wait_for_task("worker", max_wait_s=1, lease_s=10)
        self.assertEqual(claimed["status"], "ok")
        self.assertEqual(claimed["task"]["status"], "claimed")

    def test_wrong_agent_cannot_finish(self) -> None:
        task = self.bus.send_task("worker", "ownership test")
        self.bus.claim_task(task["task_id"], "worker", lease_s=10)
        with self.assertRaises(BusError):
            self.bus.finish_task(task["task_id"], "other", "done", "bad")

    def test_send_task_status_is_new(self) -> None:
        task = self.bus.send_task("worker", "test")
        self.assertEqual(task["status"], "new")

    def test_claim_task_updates_status_to_claimed(self) -> None:
        task = self.bus.send_task("worker", "test")
        claimed = self.bus.claim_task(task["task_id"], "worker", lease_s=10)
        self.assertEqual(claimed["status"], "claimed")

    def test_finish_task_makes_status_done(self) -> None:
        task = self.bus.send_task("worker", "test")
        self.bus.claim_task(task["task_id"], "worker", lease_s=10)
        result = self.bus.finish_task(task["task_id"], "worker", "done", "complete")
        self.assertEqual(result["status"], "done")

    def test_event_log_is_append_only(self) -> None:
        # Send task and record number of events
        self.bus.send_task("worker", "test1")
        events1 = list(self._read_events())
        
        # Send another task
        self.bus.send_task("worker", "test2")
        events2 = list(self._read_events())
        
        # Check that events1 is prefix of events2 (append-only)
        self.assertEqual(len(events2), len(events1) + 1)
        self.assertEqual(events2[:len(events1)], events1)
        
        # Check event types
        event_types = [e["event_type"] for e in events2]
        self.assertEqual(event_types[0], "task_sent")
        self.assertEqual(event_types[1], "task_sent")

    def test_data_dir_configurable(self) -> None:
        # Create a different temp dir
        with tempfile.TemporaryDirectory() as tmp2:
            bus2 = AgentBus(Path(tmp2))
            task = bus2.send_task("worker", "config test")
            self.assertEqual(task["status"], "new")
            
            # Check that files are in tmp2
            db_path = Path(tmp2) / "mcp_agent_bus.sqlite"
            event_log_path = Path(tmp2) / "events.jsonl"
            self.assertTrue(db_path.exists())
            self.assertTrue(event_log_path.exists())

            bus2.close()

    def test_list_tasks(self) -> None:
        self.bus.send_task("worker", "task 1")
        self.bus.send_task("worker", "task 2")
        self.bus.send_task("other", "task 3")
        
        tasks = self.bus.list_tasks()
        self.assertEqual(len(tasks["tasks"]), 3)
        
        tasks_to_worker = self.bus.list_tasks({"to": "worker"})
        self.assertEqual(len(tasks_to_worker["tasks"]), 2)

    def test_poll_for_task_empty(self) -> None:
        result = self.bus.poll_for_task("worker")
        self.assertEqual(result["status"], "empty")
        self.assertIsNone(result["task"])

    def test_poll_for_task_ok(self) -> None:
        task = self.bus.send_task("worker", "test task")
        result = self.bus.poll_for_task("worker")
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["task"])
        self.assertEqual(result["task"]["task_id"], task["task_id"])
        self.assertEqual(result["task"]["status"], "claimed")

    def test_poll_for_result_pending(self) -> None:
        task = self.bus.send_task("worker", "test task")
        self.bus.claim_task(task["task_id"], "worker")
        result = self.bus.poll_for_result(task["task_id"])
        self.assertEqual(result["status"], "pending")
        self.assertIsNotNone(result["task"])

    def test_poll_for_result_ok(self) -> None:
        task = self.bus.send_task("worker", "test task")
        self.bus.claim_task(task["task_id"], "worker")
        self.bus.finish_task(task["task_id"], "worker", "done", "complete")
        result = self.bus.poll_for_result(task["task_id"])
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["task"])
        self.assertEqual(result["task"]["status"], "done")

    def _read_events(self) -> list[dict]:
        event_log_path = Path(self.tmp.name) / "events.jsonl"
        events = []
        if event_log_path.exists():
            with event_log_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        return events


if __name__ == "__main__":
    unittest.main()
