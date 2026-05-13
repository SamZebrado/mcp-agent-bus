from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

TASK_STATES = {
    "new",
    "claimed",
    "running",
    "blocked",
    "done",
    "failed",
    "rejected",
    "cancelled",
    "expired",
}

TERMINAL_STATES = {"done", "failed", "rejected", "cancelled", "expired"}
CLAIMABLE_STATES = {"new", "expired"}


class BusError(ValueError):
    pass


def now_s() -> float:
    return time.time()


def default_data_dir() -> Path:
    return Path(os.environ.get("MCP_AGENT_BUS_DATA_DIR", "data")).resolve()


class AgentBus:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "mcp_agent_bus.sqlite"
        self.event_log_path = self.data_dir / "events.jsonl"
        self.conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_name TEXT PRIMARY KEY,
                role TEXT,
                registered_at REAL NOT NULL,
                last_seen_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                to_agent TEXT NOT NULL,
                from_agent TEXT,
                body TEXT NOT NULL,
                acceptance_criteria TEXT,
                priority INTEGER NOT NULL DEFAULT 0,
                timeout_s INTEGER,
                status TEXT NOT NULL CHECK(status IN (
                    'new','claimed','running','blocked','done','failed','rejected','cancelled','expired'
                )),
                claimed_by TEXT,
                lease_until REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL,
                summary TEXT,
                changed_files TEXT,
                evidence TEXT,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_to_status_priority
                ON tasks(to_agent, status, priority DESC, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON tasks(status, updated_at);

            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                message TEXT NOT NULL,
                evidence TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );
            """
        )

    def _event(self, event_type: str, task_id: str | None = None, agent_name: str | None = None, **payload: Any) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "ts": now_s(),
            "event_type": event_type,
            "task_id": task_id,
            "agent_name": agent_name,
            "payload": payload,
        }
        with self.event_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _json(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def _loads(self, value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    def _row_to_task(self, row: sqlite3.Row | None, include_progress: bool = True) -> dict[str, Any] | None:
        if row is None:
            return None
        task = dict(row)
        for key in ("acceptance_criteria", "changed_files", "evidence"):
            task[key] = self._loads(task.get(key))
        if include_progress:
            progress_rows = self.conn.execute(
                "SELECT agent_name, message, evidence, created_at FROM progress WHERE task_id = ? ORDER BY id",
                (task["task_id"],),
            ).fetchall()
            task["progress"] = [
                {
                    "agent_name": r["agent_name"],
                    "message": r["message"],
                    "evidence": self._loads(r["evidence"]),
                    "created_at": r["created_at"],
                }
                for r in progress_rows
            ]
        return task

    def _expire_leases(self) -> int:
        ts = now_s()
        rows = self.conn.execute(
            """
            SELECT task_id, claimed_by, status FROM tasks
            WHERE status IN ('claimed','running','blocked') AND lease_until IS NOT NULL AND lease_until < ?
            """,
            (ts,),
        ).fetchall()
        for row in rows:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'expired', updated_at = ?, lease_until = NULL
                WHERE task_id = ?
                """,
                (ts, row["task_id"]),
            )
            self._event(
                "lease_expired",
                task_id=row["task_id"],
                agent_name=row["claimed_by"],
                previous_status=row["status"],
            )
        return len(rows)

    def register_agent(self, agent_name: str, role: str | None = None) -> dict[str, Any]:
        if not agent_name:
            raise BusError("agent_name is required")
        ts = now_s()
        self.conn.execute(
            """
            INSERT INTO agents(agent_name, role, registered_at, last_seen_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(agent_name) DO UPDATE SET
                role = COALESCE(excluded.role, agents.role),
                last_seen_at = excluded.last_seen_at
            """,
            (agent_name, role, ts, ts),
        )
        self._event("agent_registered", agent_name=agent_name, role=role)
        return {"agent_name": agent_name, "role": role, "registered_at": ts}

    def send_task(
        self,
        to: str,
        body: str,
        acceptance_criteria: Any = None,
        priority: int | None = None,
        timeout_s: int | None = None,
        from_agent: str | None = None,
    ) -> dict[str, Any]:
        if not to:
            raise BusError("to is required")
        if not body:
            raise BusError("body is required")
        task_id = f"task_{uuid.uuid4().hex[:16]}"
        ts = now_s()
        self.conn.execute(
            """
            INSERT INTO tasks(
                task_id, to_agent, from_agent, body, acceptance_criteria, priority,
                timeout_s, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (
                task_id,
                to,
                from_agent,
                body,
                self._json(acceptance_criteria),
                int(priority or 0),
                timeout_s,
                ts,
                ts,
            ),
        )
        self._event(
            "task_sent",
            task_id=task_id,
            agent_name=from_agent,
            to=to,
            priority=int(priority or 0),
        )
        return self.get_task(task_id)

    def claim_task(self, task_id: str, agent_name: str, lease_s: int | None = None) -> dict[str, Any]:
        if not task_id or not agent_name:
            raise BusError("task_id and agent_name are required")
        lease = int(lease_s or 300)
        ts = now_s()
        lease_until = ts + lease
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_leases()
            row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise BusError(f"task not found: {task_id}")
            if row["to_agent"] != agent_name:
                raise BusError(f"task {task_id} is assigned to {row['to_agent']}, not {agent_name}")
            if row["status"] not in CLAIMABLE_STATES:
                raise BusError(f"task {task_id} is not claimable; current status is {row['status']}")
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'claimed', claimed_by = ?, lease_until = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (agent_name, lease_until, ts, task_id),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self._event("task_claimed", task_id=task_id, agent_name=agent_name, lease_until=lease_until)
        return self.get_task(task_id)

    def wait_for_task(self, agent_name: str, max_wait_s: int | None = None, lease_s: int | None = None) -> dict[str, Any]:
        deadline = now_s() + int(max_wait_s if max_wait_s is not None else 30)
        while True:
            self.conn.execute("BEGIN IMMEDIATE")
            task_id = None
            try:
                self._expire_leases()
                row = self.conn.execute(
                    """
                    SELECT task_id FROM tasks
                    WHERE to_agent = ? AND status IN ('new','expired')
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    """,
                    (agent_name,),
                ).fetchone()
                if row:
                    task_id = row["task_id"]
                    lease = int(lease_s or 300)
                    lease_until = now_s() + lease
                    self.conn.execute(
                        """
                        UPDATE tasks
                        SET status = 'claimed', claimed_by = ?, lease_until = ?, updated_at = ?
                        WHERE task_id = ?
                        """,
                        (agent_name, lease_until, now_s(), task_id),
                    )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            if task_id:
                self._event("task_claimed", task_id=task_id, agent_name=agent_name)
                return {"status": "ok", "task": self.get_task(task_id)}
            if now_s() >= deadline:
                return {"status": "timeout", "task": None}
            time.sleep(min(0.25, max(0.0, deadline - now_s())))

    def append_progress(self, task_id: str, agent_name: str, message: str, evidence: Any = None) -> dict[str, Any]:
        if not message:
            raise BusError("message is required")
        ts = now_s()
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise BusError(f"task not found: {task_id}")
        if row["claimed_by"] != agent_name:
            raise BusError(f"task {task_id} is claimed by {row['claimed_by']}, not {agent_name}")
        if row["status"] in TERMINAL_STATES:
            raise BusError(f"task {task_id} is terminal: {row['status']}")
        self.conn.execute(
            "INSERT INTO progress(task_id, agent_name, message, evidence, created_at) VALUES(?, ?, ?, ?, ?)",
            (task_id, agent_name, message, self._json(evidence), ts),
        )
        next_status = "running" if row["status"] == "claimed" else row["status"]
        self.conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (next_status, ts, task_id),
        )
        self._event("progress_appended", task_id=task_id, agent_name=agent_name, message=message, evidence=evidence)
        return self.get_task(task_id)

    def finish_task(
        self,
        task_id: str,
        agent_name: str,
        status: str,
        summary: str,
        changed_files: Any = None,
        evidence: Any = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"blocked", "done", "failed", "rejected", "cancelled", "expired"}:
            raise BusError("finish status must be one of blocked, done, failed, rejected, cancelled, expired")
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise BusError(f"task not found: {task_id}")
        if row["claimed_by"] != agent_name:
            raise BusError(f"task {task_id} is claimed by {row['claimed_by']}, not {agent_name}")
        ts = now_s()
        finished_at = ts if status in TERMINAL_STATES else None
        self.conn.execute(
            """
            UPDATE tasks
            SET status = ?, summary = ?, changed_files = ?, evidence = ?, error_message = ?,
                lease_until = NULL, updated_at = ?, finished_at = COALESCE(?, finished_at)
            WHERE task_id = ?
            """,
            (
                status,
                summary,
                self._json(changed_files),
                self._json(evidence),
                error_message,
                ts,
                finished_at,
                task_id,
            ),
        )
        self._event(
            "task_finished",
            task_id=task_id,
            agent_name=agent_name,
            status=status,
            summary=summary,
            changed_files=changed_files,
            evidence=evidence,
            error_message=error_message,
        )
        return self.get_task(task_id)

    def wait_for_result(self, task_id: str, max_wait_s: int | None = None) -> dict[str, Any]:
        deadline = now_s() + int(max_wait_s if max_wait_s is not None else 30)
        while True:
            self._expire_leases()
            task = self.get_task(task_id)
            if task is None:
                raise BusError(f"task not found: {task_id}")
            if task["status"] in TERMINAL_STATES:
                return {"status": "ok", "task": task}
            if now_s() >= deadline:
                return {"status": "timeout", "task": task}
            time.sleep(min(0.25, max(0.0, deadline - now_s())))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_task(row)

    def list_tasks(self, filter: dict[str, Any] | None = None) -> dict[str, Any]:
        self._expire_leases()
        filter = filter or {}
        clauses = []
        args: list[Any] = []
        if filter.get("to"):
            clauses.append("to_agent = ?")
            args.append(filter["to"])
        if filter.get("status"):
            statuses = filter["status"]
            if isinstance(statuses, str):
                statuses = [statuses]
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            args.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = int(filter.get("limit", 50))
        rows = self.conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return {"tasks": [self._row_to_task(row, include_progress=False) for row in rows]}

    def poll_for_task(self, agent_name: str, lease_s: int | None = None) -> dict[str, Any]:
        self.conn.execute("BEGIN IMMEDIATE")
        task_id = None
        try:
            self._expire_leases()
            row = self.conn.execute(
                """
                SELECT task_id FROM tasks
                WHERE to_agent = ? AND status IN ('new','expired')
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (agent_name,),
            ).fetchone()
            if row:
                task_id = row["task_id"]
                lease = int(lease_s or 300)
                lease_until = now_s() + lease
                self.conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'claimed', claimed_by = ?, lease_until = ?, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (agent_name, lease_until, now_s(), task_id),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        if task_id:
            self._event("task_claimed", task_id=task_id, agent_name=agent_name)
            return {"status": "ok", "task": self.get_task(task_id)}
        return {"status": "empty", "task": None}

    def poll_for_result(self, task_id: str) -> dict[str, Any]:
        self._expire_leases()
        task = self.get_task(task_id)
        if task is None:
            raise BusError(f"task not found: {task_id}")
        if task["status"] in TERMINAL_STATES:
            return {"status": "ok", "task": task}
        return {"status": "pending", "task": task}
