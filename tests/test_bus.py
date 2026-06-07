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

    def test_codex_sync_can_register_and_send_task(self) -> None:
        result = self.bus.codex_bus_sync(
            agent_name="planner",
            role="planner",
            send=[{"to": "worker", "body": "draft README", "priority": 3}],
        )
        self.assertTrue(result["compact"])
        self.assertEqual(result["agent"]["agent_name"], "planner")
        self.assertEqual(len(result["send"]), 1)
        self.assertEqual(result["send"][0]["status"], "new")
        self.assertEqual(result["send"][0]["body"], "draft README")

    def test_codex_sync_can_claim_one_task(self) -> None:
        self.bus.send_task("worker", "claim me")
        result = self.bus.codex_bus_sync(
            agent_name="worker",
            claim={"enabled": True, "lease_s": 30, "limit": 1},
        )
        self.assertEqual(len(result["claim"]), 1)
        self.assertEqual(result["claim"][0]["status"], "ok")
        self.assertEqual(result["claim"][0]["task"]["status"], "claimed")
        self.assertEqual(result["claim"][0]["task"]["body"], "claim me")

    def test_codex_sync_can_finish_task(self) -> None:
        task = self.bus.send_task("worker", "finish me")
        self.bus.claim_task(task["task_id"], "worker")
        result = self.bus.codex_bus_sync(
            agent_name="worker",
            finish=[{"task_id": task["task_id"], "status": "done", "summary": "finished"}],
        )
        self.assertEqual(len(result["finish"]), 1)
        self.assertEqual(result["finish"][0]["task_id"], task["task_id"])
        self.assertEqual(result["finish"][0]["status"], "done")
        self.assertEqual(result["finish"][0]["summary"], "finished")

    def test_codex_sync_can_watch_pending_and_done_task(self) -> None:
        pending_task = self.bus.send_task("worker", "pending task")
        done_task = self.bus.send_task("worker", "done task")
        self.bus.claim_task(done_task["task_id"], "worker")
        self.bus.finish_task(done_task["task_id"], "worker", "done", "all set")

        result = self.bus.codex_bus_sync(
            agent_name="watcher",
            watch=[pending_task["task_id"], done_task["task_id"]],
        )

        self.assertEqual(len(result["watch"]), 2)
        statuses = {item["task_id"]: item["status"] for item in result["watch"]}
        self.assertEqual(statuses[pending_task["task_id"]], "pending")
        self.assertEqual(statuses[done_task["task_id"]], "ok")

    def test_codex_sync_compact_omits_large_progress_and_evidence(self) -> None:
        task = self.bus.send_task("worker", "body text")
        self.bus.claim_task(task["task_id"], "worker")
        self.bus.append_progress(task["task_id"], "worker", "step 1", evidence={"log": "x" * 2000})
        self.bus.finish_task(
            task["task_id"],
            "worker",
            "done",
            "summary text",
            evidence={"blob": "y" * 2000},
        )

        compact = self.bus.codex_bus_sync(
            agent_name="watcher",
            watch=[task["task_id"]],
            list={"to": "worker"},
            compact=True,
        )
        watch_task = compact["watch"][0]["task"]
        self.assertEqual(set(watch_task.keys()), {"task_id", "status", "summary", "body"})
        self.assertEqual(set(compact["list"]["tasks"][0].keys()), {"task_id", "status", "summary", "body"})

        full = self.bus.codex_bus_sync(
            agent_name="watcher",
            watch=[task["task_id"]],
            compact=False,
        )
        self.assertIn("progress", full["watch"][0]["task"])
        self.assertIn("evidence", full["watch"][0]["task"])

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

    def test_cleanup_event_log_archives_old_lines(self) -> None:
        event_log = Path(self.tmp.name) / "events.jsonl"
        event_log.write_text(
            "".join(f'{{"n": {idx}}}\n' for idx in range(20)),
            encoding="utf-8",
        )

        result = self.bus.cleanup_event_log(keep_last_lines=5, archive=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["kept_lines"], 5)
        self.assertEqual(result["archived_lines"], 15)
        self.assertIsNotNone(result["archived_path"])
        self.assertEqual(len(event_log.read_text(encoding="utf-8").splitlines()), 6)

    def test_cleanup_event_log_skips_within_limit(self) -> None:
        event_log = Path(self.tmp.name) / "events.jsonl"
        event_log.write_text(
            "".join(f'{{"n": {idx}}}\n' for idx in range(5)),
            encoding="utf-8",
        )

        result = self.bus.cleanup_event_log(keep_last_lines=10)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "line_count_within_limit")

    def test_cleanup_event_log_no_archive(self) -> None:
        event_log = Path(self.tmp.name) / "events.jsonl"
        event_log.write_text(
            "".join(f'{{"n": {idx}}}\n' for idx in range(20)),
            encoding="utf-8",
        )

        result = self.bus.cleanup_event_log(keep_last_lines=5, archive=False)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["archived_lines"], 15)
        self.assertIsNone(result["archived_path"])
        self.assertEqual(len(event_log.read_text(encoding="utf-8").splitlines()), 6)

    def test_finish_task_rejects_terminal_overwrite(self) -> None:
        task = self.bus.send_task("worker", "test")
        task_id = task["task_id"]
        self.bus.claim_task(task_id, "worker")
        self.bus.finish_task(task_id, "worker", "done", "done once")

        with self.assertRaises(BusError) as ctx:
            self.bus.finish_task(task_id, "worker", "failed", "overwrite")
        self.assertIn("terminal", str(ctx.exception))

    def test_append_progress_rejects_terminal_task(self) -> None:
        task = self.bus.send_task("worker", "test")
        task_id = task["task_id"]
        self.bus.claim_task(task_id, "worker")
        self.bus.finish_task(task_id, "worker", "done", "done once")

        with self.assertRaises(BusError) as ctx:
            self.bus.append_progress(task_id, "worker", "should fail")
        self.assertIn("terminal", str(ctx.exception))

    def test_send_task_rolls_back_if_event_fails(self) -> None:
        original_event = self.bus._event

        def fail_event(*args, **kwargs):
            raise RuntimeError("event write failed")

        self.bus._event = fail_event
        try:
            with self.assertRaises(RuntimeError):
                self.bus.send_task("worker", "test")
        finally:
            self.bus._event = original_event

        tasks = self.bus.list_tasks()
        self.assertEqual(len(tasks["tasks"]), 0)

    def test_claim_task_rolls_back_if_event_fails(self) -> None:
        task = self.bus.send_task("worker", "test")
        original_event = self.bus._event

        def fail_event(*args, **kwargs):
            raise RuntimeError("event write failed")

        self.bus._event = fail_event
        try:
            with self.assertRaises(RuntimeError):
                self.bus.claim_task(task["task_id"], "worker")
        finally:
            self.bus._event = original_event

        reloaded = self.bus.get_task(task["task_id"])
        self.assertEqual(reloaded["status"], "new")
        self.assertIsNone(reloaded["claimed_by"])

    def test_task_claimed_event_inside_transaction(self) -> None:
        task = self.bus.send_task("worker", "test")
        original_event = self.bus._event
        events = []

        def track_event(*args, **kwargs):
            events.append(("inside", args, kwargs))

        self.bus._event = track_event
        try:
            result = self.bus.claim_task(task["task_id"], "worker")
            self.assertEqual(result["claimed_by"], "worker")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][0], "inside")
        finally:
            self.bus._event = original_event

    def test_poll_for_task_event_inside_transaction(self) -> None:
        self.bus.send_task("worker", "test")
        original_event = self.bus._event
        events = []

        def track_event(*args, **kwargs):
            events.append(("inside", args, kwargs))

        self.bus._event = track_event
        try:
            result = self.bus.poll_for_task("worker")
            self.assertEqual(result["status"], "ok")
            self.assertIn("task", result)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][0], "inside")
        finally:
            self.bus._event = original_event


if __name__ == "__main__":
    unittest.main()
