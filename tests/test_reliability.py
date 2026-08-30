from __future__ import annotations

import json
import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mcp_agent_bus.bus import AgentBus, BusError, SCHEMA_VERSION
from mcp_agent_bus.diagnostics import run_doctor


def _send_same_request(data_dir: str, barrier: object, queue: object) -> None:
    bus = AgentBus(Path(data_dir))
    try:
        barrier.wait()
        task = bus.send_task(
            "worker",
            "same request",
            acceptance_criteria={"ok": True, "order": [1, 2]},
            priority=0,
            timeout_s=30,
            from_agent="planner",
            client_request_id="request-1",
        )
        queue.put(("ok", task["task_id"]))
    except Exception as exc:  # pragma: no cover - reported to parent
        queue.put(("error", repr(exc)))
    finally:
        bus.close()


def _claim_once(data_dir: str, barrier: object, queue: object) -> None:
    bus = AgentBus(Path(data_dir))
    try:
        barrier.wait()
        result = bus.poll_for_task("worker", lease_s=30)
        queue.put((result["status"], result["task"]["task_id"] if result["task"] else None))
    except Exception as exc:  # pragma: no cover - reported to parent
        queue.put(("error", repr(exc)))
    finally:
        bus.close()


class ReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bus = AgentBus(self.root)

    def tearDown(self) -> None:
        self.bus.close()
        self.tmp.cleanup()

    def test_idempotent_send_returns_original_without_duplicate_event(self) -> None:
        args = {
            "to": "worker",
            "body": "work",
            "acceptance_criteria": {"b": 2, "a": [1, 2]},
            "priority": None,
            "timeout_s": 60,
            "from_agent": "planner",
            "client_request_id": "req-1",
        }
        first = self.bus.send_task(**args)
        second = self.bus.send_task(**args)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(len(self.bus.list_tasks()["tasks"]), 1)
        count = self.bus.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'task_sent'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_idempotency_key_conflict_rejects_different_payload(self) -> None:
        self.bus.send_task(
            "worker", "one", from_agent="planner", client_request_id="req-1"
        )
        with self.assertRaisesRegex(BusError, "different send payload"):
            self.bus.send_task(
                "worker", "two", from_agent="planner", client_request_id="req-1"
            )
        self.assertEqual(len(self.bus.list_tasks()["tasks"]), 1)

    def test_idempotency_key_requires_sender(self) -> None:
        with self.assertRaisesRegex(BusError, "from_agent is required"):
            self.bus.send_task("worker", "one", client_request_id="req-1")

    def test_sender_can_cancel_only_unclaimed_task_idempotently(self) -> None:
        task = self.bus.send_task("worker", "one", from_agent="planner")
        with self.assertRaises(BusError):
            self.bus.cancel_task(task["task_id"], "other")
        cancelled = self.bus.cancel_task(task["task_id"], "planner", "retry duplicate")
        again = self.bus.cancel_task(task["task_id"], "planner", "retry duplicate")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(again["status"], "cancelled")
        count = self.bus.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'task_cancelled'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_active_task_cannot_be_cancelled(self) -> None:
        task = self.bus.send_task("worker", "one", from_agent="planner")
        self.bus.claim_task(task["task_id"], "worker", lease_s=30)
        with self.assertRaisesRegex(BusError, "cannot be cancelled"):
            self.bus.cancel_task(task["task_id"], "planner")

    def test_planner_review_is_separate_immutable_and_idempotent(self) -> None:
        task = self.bus.send_task("worker", "one", from_agent="planner")
        self.bus.claim_task(task["task_id"], "worker")
        self.bus.finish_task(task["task_id"], "worker", "done", "complete")
        with self.assertRaises(BusError):
            self.bus.accept_task_result(task["task_id"], "other")
        accepted = self.bus.accept_task_result(task["task_id"], "planner", "verified")
        repeated = self.bus.accept_task_result(task["task_id"], "planner", "verified")
        self.assertEqual(accepted["status"], "done")
        self.assertEqual(accepted["result_review"], "accepted")
        self.assertEqual(repeated["result_review"], "accepted")
        with self.assertRaisesRegex(BusError, "review is immutable"):
            self.bus.reject_task_result(task["task_id"], "planner", "changed mind")
        count = self.bus.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'task_result_reviewed'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_only_done_task_is_reviewable(self) -> None:
        task = self.bus.send_task("worker", "one", from_agent="planner")
        with self.assertRaisesRegex(BusError, "only done tasks"):
            self.bus.reject_task_result(task["task_id"], "planner")

    def test_doctor_missing_store_does_not_create_it(self) -> None:
        missing = self.root / "missing"
        report = run_doctor(missing)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(missing.exists())

    def test_doctor_reports_healthy_store_and_authority(self) -> None:
        self.bus.register_agent("planner", "planner")
        report = run_doctor(self.root)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["schema_version"], SCHEMA_VERSION)
        self.assertEqual(report["authoritative_store"], "sqlite")
        self.assertEqual(report["event_consistency"]["sqlite_only_event_ids"], [])

    def test_doctor_counts_archived_event_mirror_as_consistent(self) -> None:
        for index in range(4):
            self.bus.send_task("worker", f"task-{index}", from_agent="planner")
        self.bus.cleanup_event_log(keep_last_lines=1, archive=True)
        report = run_doctor(self.root)
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(report["event_consistency"]["archive_file_count"], 0)
        self.assertEqual(report["event_consistency"]["sqlite_only_event_ids"], [])

    def test_committed_sqlite_event_survives_mirror_failure(self) -> None:
        original = self.bus.event_log_path
        bad_path = self.root / "not-a-file"
        bad_path.mkdir()
        self.bus.event_log_path = bad_path
        task = self.bus.send_task("worker", "one", from_agent="planner")
        self.assertIsNotNone(self.bus.get_task(task["task_id"]))
        count = self.bus.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 1)
        self.bus.event_log_path = original
        self.bus._sync_event_log()
        events = [json.loads(line) for line in original.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)

    def test_additive_migration_imports_legacy_event_log(self) -> None:
        legacy = self.root / "legacy"
        legacy.mkdir()
        conn = sqlite3.connect(legacy / "mcp_agent_bus.sqlite")
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY, to_agent TEXT NOT NULL, from_agent TEXT,
                body TEXT NOT NULL, acceptance_criteria TEXT, priority INTEGER NOT NULL DEFAULT 0,
                timeout_s INTEGER, status TEXT NOT NULL, claimed_by TEXT, lease_until REAL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL,
                summary TEXT, changed_files TEXT, evidence TEXT, error_message TEXT
            );
            PRAGMA user_version=0;
            """
        )
        conn.close()
        legacy_event = {
            "event_id": "legacy-event-1",
            "ts": 1.0,
            "event_type": "task_sent",
            "task_id": "legacy-task",
            "agent_name": "planner",
            "payload": {"to": "worker"},
        }
        (legacy / "events.jsonl").write_text(
            json.dumps(legacy_event) + "\n", encoding="utf-8"
        )
        migrated = AgentBus(legacy)
        try:
            columns = {
                row["name"] for row in migrated.conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            self.assertIn("client_request_id", columns)
            self.assertIn("result_review", columns)
            self.assertEqual(
                migrated.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            self.assertEqual(
                migrated.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
            )
        finally:
            migrated.close()

    def test_concurrent_same_key_send_and_claim_races(self) -> None:
        self.bus.close()
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        senders = [
            ctx.Process(target=_send_same_request, args=(str(self.root), barrier, queue))
            for _ in range(2)
        ]
        for process in senders:
            process.start()
        for process in senders:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        sent = [queue.get(timeout=2) for _ in senders]
        self.assertEqual({item[0] for item in sent}, {"ok"})
        self.assertEqual(len({item[1] for item in sent}), 1)

        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        claimers = [
            ctx.Process(target=_claim_once, args=(str(self.root), barrier, queue))
            for _ in range(2)
        ]
        for process in claimers:
            process.start()
        for process in claimers:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        claimed = [queue.get(timeout=2) for _ in claimers]
        self.assertEqual(sorted(item[0] for item in claimed), ["empty", "ok"])

        conn = sqlite3.connect(self.root / "mcp_agent_bus.sqlite")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM events WHERE event_type='task_sent'").fetchone()[0],
            1,
        )
        conn.close()
        for line in (self.root / "events.jsonl").read_text(encoding="utf-8").splitlines():
            json.loads(line)
        self.bus = AgentBus(self.root)


if __name__ == "__main__":
    unittest.main()
