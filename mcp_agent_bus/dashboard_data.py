from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"done", "failed", "rejected", "cancelled", "expired"}


def parse_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def read_events(data_dir: Path, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    path = data_dir / "events.jsonl"
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"event_type": "invalid_jsonl", "raw": line, "ts": None}
            if task_id is None or event.get("task_id") == task_id:
                events.append(event)
    events.sort(key=lambda item: item.get("ts") or 0)
    return events[-limit:]


class DashboardStore:
    """Read-only view over mcp-agent-bus SQLite and event log data."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.db_path = self.data_dir / "mcp_agent_bus.sqlite"

    def db_exists(self) -> bool:
        return self.db_path.exists()

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def overview(self, status: str | None = None, to: str | None = None, q: str | None = None) -> dict[str, Any]:
        conn = self._connect()
        if conn is None:
            return {
                "data_dir": str(self.data_dir),
                "db_path": str(self.db_path),
                "db_exists": False,
                "agents": [],
                "tasks": [],
                "events": read_events(self.data_dir, limit=100),
                "statuses": [],
                "assignees": [],
                "now": time.time(),
            }
        try:
            tasks = self._list_tasks(conn, status=status, to=to, q=q)
            agents = self._list_agents(conn)
            return {
                "data_dir": str(self.data_dir),
                "db_path": str(self.db_path),
                "db_exists": True,
                "agents": self._with_agent_counts(agents, tasks, conn),
                "tasks": tasks,
                "events": read_events(self.data_dir, limit=100),
                "statuses": self._distinct(conn, "status", "tasks"),
                "assignees": self._distinct(conn, "to_agent", "tasks"),
                "now": time.time(),
            }
        finally:
            conn.close()

    def task_detail(self, task_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            task = self._task_from_row(row)
            progress_rows = conn.execute(
                "SELECT agent_name, message, evidence, created_at FROM progress WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
            task["progress"] = [
                {
                    "agent_name": item["agent_name"],
                    "message": item["message"],
                    "evidence": parse_json(item["evidence"]),
                    "created_at": item["created_at"],
                }
                for item in progress_rows
            ]
            return {
                "task": task,
                "events": read_events(self.data_dir, task_id=task_id, limit=500),
                "raw": task,
                "now": time.time(),
                "data_dir": str(self.data_dir),
            }
        finally:
            conn.close()

    def _list_tasks(
        self,
        conn: sqlite3.Connection,
        status: str | None = None,
        to: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if to:
            clauses.append("to_agent = ?")
            args.append(to)
        if q:
            clauses.append(
                "(task_id LIKE ? OR body LIKE ? OR summary LIKE ? OR from_agent LIKE ? OR to_agent LIKE ?)"
            )
            like = f"%{q}%"
            args.extend([like, like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY updated_at DESC, created_at DESC LIMIT 500",
            args,
        ).fetchall()
        return [self._task_from_row(row, include_title=True) for row in rows]

    def _list_agents(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT * FROM agents ORDER BY last_seen_at DESC, agent_name ASC").fetchall()
        return [dict(row) for row in rows]

    def _with_agent_counts(
        self, agents: list[dict[str, Any]], filtered_tasks: list[dict[str, Any]], conn: sqlite3.Connection
    ) -> list[dict[str, Any]]:
        known = {agent["agent_name"]: agent for agent in agents}
        for task in filtered_tasks:
            if task["to_agent"] and task["to_agent"] not in known:
                known[task["to_agent"]] = {
                    "agent_name": task["to_agent"],
                    "role": None,
                    "registered_at": None,
                    "last_seen_at": None,
                }
        rows = conn.execute(
            """
            SELECT to_agent, status, COUNT(*) AS count
            FROM tasks
            GROUP BY to_agent, status
            """
        ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["to_agent"], {})[row["status"]] = int(row["count"])
        now = time.time()
        result = []
        for agent_name, agent in sorted(known.items()):
            status_counts = counts.get(agent_name, {})
            last_seen = agent.get("last_seen_at")
            if last_seen is None:
                inferred_status = "unknown"
            elif now - float(last_seen) <= 15 * 60:
                inferred_status = "active"
            else:
                inferred_status = "stale"
            enriched = dict(agent)
            enriched["inferred_status"] = inferred_status
            enriched["claimed_count"] = status_counts.get("claimed", 0)
            enriched["running_count"] = status_counts.get("running", 0)
            enriched["finished_count"] = sum(status_counts.get(state, 0) for state in TERMINAL_STATES)
            enriched["failed_count"] = status_counts.get("failed", 0)
            result.append(enriched)
        return result

    def _task_from_row(self, row: sqlite3.Row, include_title: bool = False) -> dict[str, Any]:
        task = dict(row)
        for key in ("acceptance_criteria", "changed_files", "evidence"):
            task[key] = parse_json(task.get(key))
        if include_title:
            task["title"] = task.get("summary") or first_line(task.get("body")) or task["task_id"]
        return task

    def _distinct(self, conn: sqlite3.Connection, column: str, table: str) -> list[str]:
        rows = conn.execute(
            f"SELECT DISTINCT {column} AS value FROM {table} WHERE {column} IS NOT NULL ORDER BY {column}"
        ).fetchall()
        return [row["value"] for row in rows if row["value"]]


def first_line(value: str | None, limit: int = 90) -> str | None:
    if not value:
        return None
    line = value.strip().splitlines()[0].strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1] + "…"
