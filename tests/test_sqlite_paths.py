from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mcp_agent_bus.bus import AgentBus
from mcp_agent_bus.dashboard_data import DashboardStore
from mcp_agent_bus.diagnostics import run_doctor


# Question marks are valid POSIX filenames, but are not valid on Windows.
DATA_DIR_NAMES = ("data#archive", "data%23archive", "data%3Farchive", "data with spaces", "数据")
if os.name != "nt":
    DATA_DIR_NAMES += ("data?archive",)


class SqlitePathTests(unittest.TestCase):
    def _create_task(self, data_dir: Path) -> str:
        bus = AgentBus(data_dir)
        try:
            return bus.send_task("worker", "synthetic path test", from_agent="planner")["task_id"]
        finally:
            bus.close()

    def test_doctor_uses_literal_data_directory_without_creating_siblings(self) -> None:
        for name in DATA_DIR_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data_dir = root / name
                task_id = self._create_task(data_dir)
                before = set(root.iterdir())

                report = run_doctor(data_dir)

                self.assertEqual(set(root.iterdir()), before)
                self.assertEqual(report["status"], "PASS", report["checks"])
                self.assertEqual([task["task_id"] for task in report["recent_tasks"]], [task_id])
                self.assertEqual(report["task_counts"]["new"], 1)

    def test_dashboard_uses_literal_data_directory_and_remains_read_only(self) -> None:
        for name in DATA_DIR_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data_dir = root / name
                task_id = self._create_task(data_dir)
                before = set(root.iterdir())
                store = DashboardStore(data_dir)

                overview = store.overview()
                self.assertEqual([task["task_id"] for task in overview["tasks"]], [task_id])
                self.assertEqual(store.task_detail(task_id)["task"]["task_id"], task_id)
                conn = store._connect()
                self.assertIsNotNone(conn)
                try:
                    self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
                    # URI mode=ro must remain effective independently of query_only.
                    conn.execute("PRAGMA query_only=OFF")
                    with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                        conn.execute("DELETE FROM tasks")
                finally:
                    conn.close()
                self.assertEqual(set(root.iterdir()), before)

    def test_percent_encoded_path_does_not_alias_another_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stores = [(root / name) for name in ("data#archive", "data%23archive")]
            task_ids = [self._create_task(path) for path in stores]
            for path, task_id in zip(stores, task_ids):
                with self.subTest(path=path.name):
                    report = run_doctor(path)
                    self.assertEqual([task["task_id"] for task in report["recent_tasks"]], [task_id])
                    overview = DashboardStore(path).overview()
                    self.assertEqual([task["task_id"] for task in overview["tasks"]], [task_id])

    def test_missing_database_in_special_directory_is_not_created(self) -> None:
        for name in DATA_DIR_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data_dir = root / name
                data_dir.mkdir()

                self.assertEqual(run_doctor(data_dir)["status"], "FAIL")
                self.assertFalse(DashboardStore(data_dir).overview()["db_exists"])
                self.assertEqual(set(root.iterdir()), {data_dir})
                self.assertEqual(list(data_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
