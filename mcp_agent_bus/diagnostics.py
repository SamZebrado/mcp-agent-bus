from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .bus import SCHEMA_VERSION, TASK_STATES, default_data_dir


def _check(name: str, status: str, message: str, action: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "status": status, "message": message}
    if action:
        result["action"] = action
    return result


def _parse_event_log(path: Path) -> tuple[list[dict[str, Any]], int, list[str]]:
    events: list[dict[str, Any]] = []
    invalid = 0
    ids: list[str] = []
    if not path.exists():
        return events, invalid, ids
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                    event_id = str(event["event_id"])
                    events.append(event)
                    ids.append(event_id)
                except (KeyError, TypeError, json.JSONDecodeError):
                    invalid += 1
    except (OSError, UnicodeError):
        invalid += 1
    return events, invalid, ids


def run_doctor(
    data_dir: Path | None = None,
    *,
    recent_limit: int = 5,
    stranded_after_s: int = 3600,
) -> dict[str, Any]:
    """Inspect an existing bus store without creating or migrating it."""
    root = (data_dir or default_data_dir()).resolve()
    db_path = root / "mcp_agent_bus.sqlite"
    event_path = root / "events.jsonl"
    checks: list[dict[str, Any]] = []
    db_event_ids: list[str] = []
    report: dict[str, Any] = {
        "data_dir": str(root),
        "db_path": str(db_path),
        "event_log_path": str(event_path),
        "authoritative_store": "sqlite",
        "event_log_role": "best_effort_mirror",
        "checks": checks,
        "schema_version": None,
        "expected_schema_version": SCHEMA_VERSION,
        "agents": [],
        "task_counts": {state: 0 for state in sorted(TASK_STATES)},
        "expired_leases": [],
        "stranded_tasks": [],
        "possible_duplicates": [],
        "recent_tasks": [],
        "recent_events": [],
    }

    if not root.exists():
        checks.append(
            _check(
                "data_dir", "FAIL", "data directory does not exist",
                "Set MCP_AGENT_BUS_DATA_DIR correctly or initialize the bus with an explicit data directory.",
            )
        )
        report["status"] = "FAIL"
        return report
    if not root.is_dir():
        checks.append(_check("data_dir", "FAIL", "configured data path is not a directory"))
        report["status"] = "FAIL"
        return report

    readable = os.access(root, os.R_OK | os.X_OK)
    try:
        with tempfile.NamedTemporaryFile(prefix=".doctor-", dir=root, delete=True):
            writable = True
    except OSError:
        writable = False
    checks.append(
        _check(
            "data_dir",
            "PASS" if readable and writable else "FAIL",
            f"readable={readable}, writable={writable}",
            None if readable and writable else "Fix directory ownership/permissions before running the bus.",
        )
    )

    if not db_path.exists():
        checks.append(
            _check(
                "sqlite", "FAIL", "SQLite database is missing; doctor did not create it",
                "Verify MCP_AGENT_BUS_DATA_DIR or start the bus once to initialize a new store.",
            )
        )
        report["status"] = "FAIL"
        return report

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
        integrity = [str(row[0]) for row in integrity_rows]
        integrity_ok = integrity == ["ok"]
        checks.append(
            _check(
                "sqlite_integrity", "PASS" if integrity_ok else "FAIL",
                "; ".join(integrity[:10]),
                None if integrity_ok else "Stop writers, back up the data directory, and recover SQLite before continuing.",
            )
        )
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        report["schema_version"] = version
        checks.append(
            _check(
                "schema_version", "PASS" if version == SCHEMA_VERSION else "WARN",
                f"found={version}, expected={SCHEMA_VERSION}",
                None if version == SCHEMA_VERSION else "Start the current bus once to run its additive schema migration.",
            )
        )
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"agents", "tasks", "progress", "events"}
        missing_tables = sorted(required - tables)
        checks.append(
            _check(
                "schema_tables", "PASS" if not missing_tables else "FAIL",
                "all required tables present" if not missing_tables else f"missing={missing_tables}",
                None if not missing_tables else "Run the current bus migration after backing up the data directory.",
            )
        )

        if "agents" in tables:
            report["agents"] = [
                dict(row) for row in conn.execute(
                    "SELECT agent_name, role, registered_at, last_seen_at FROM agents ORDER BY last_seen_at DESC"
                ).fetchall()
            ]
        if "tasks" in tables:
            task_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
                report["task_counts"][str(row["status"])] = int(row["n"])
            now = time.time()
            report["expired_leases"] = [
                dict(row) for row in conn.execute(
                    """
                    SELECT task_id, to_agent, claimed_by, status, lease_until
                    FROM tasks WHERE status IN ('claimed','running','blocked')
                    AND lease_until IS NOT NULL AND lease_until < ? ORDER BY lease_until
                    """,
                    (now,),
                ).fetchall()
            ]
            report["stranded_tasks"] = [
                dict(row) for row in conn.execute(
                    """
                    SELECT task_id, from_agent, to_agent, status, created_at, body
                    FROM tasks WHERE status = 'new' AND created_at < ? ORDER BY created_at
                    """,
                    (now - max(0, stranded_after_s),),
                ).fetchall()
            ]
            duplicate_rows = conn.execute(
                """
                SELECT from_agent, to_agent, body, COUNT(*) AS task_count,
                       GROUP_CONCAT(task_id) AS task_ids
                FROM tasks WHERE status IN ('new','expired')
                GROUP BY from_agent, to_agent, body HAVING COUNT(*) > 1
                ORDER BY task_count DESC
                """
            ).fetchall()
            report["possible_duplicates"] = [dict(row) for row in duplicate_rows]
            review_select = "result_review" if "result_review" in task_columns else "NULL AS result_review"
            report["recent_tasks"] = [
                dict(row) for row in conn.execute(
                    f"""
                    SELECT task_id, from_agent, to_agent, status, {review_select}, created_at, updated_at
                    FROM tasks ORDER BY updated_at DESC LIMIT ?
                    """,
                    (max(0, recent_limit),),
                ).fetchall()
            ]

        if "events" in tables:
            event_rows = conn.execute(
                """
                SELECT event_id, ts, event_type, task_id, agent_name, payload
                FROM events ORDER BY ts DESC, event_id DESC LIMIT ?
                """,
                (max(0, recent_limit),),
            ).fetchall()
            report["recent_events"] = [
                {
                    **dict(row),
                    "payload": json.loads(row["payload"]),
                }
                for row in event_rows
            ]
            db_event_ids = [
                str(row[0]) for row in conn.execute("SELECT event_id FROM events").fetchall()
            ]
    except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        checks.append(
            _check(
                "sqlite_read", "FAIL", f"cannot inspect SQLite: {exc}",
                "Verify the path and permissions; preserve the files before attempting recovery.",
            )
        )
    finally:
        if conn is not None:
            conn.close()

    try:
        rw = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True, timeout=5, isolation_level=None)
        rw.execute("BEGIN IMMEDIATE")
        rw.execute("ROLLBACK")
        rw.close()
        checks.append(_check("sqlite_writable", "PASS", "bounded write-lock probe succeeded"))
    except sqlite3.Error as exc:
        checks.append(
            _check(
                "sqlite_writable", "FAIL", f"write-lock probe failed: {exc}",
                "Stop conflicting writers or fix database/directory permissions.",
            )
        )

    mirror_events, invalid_lines, mirror_ids = _parse_event_log(event_path)
    archive_dir = root / "event_archives"
    archive_paths = sorted(archive_dir.glob("*.jsonl")) if archive_dir.is_dir() else []
    for archive_path in archive_paths:
        archived_events, archived_invalid, archived_ids = _parse_event_log(archive_path)
        mirror_events.extend(archived_events)
        invalid_lines += archived_invalid
        mirror_ids.extend(archived_ids)
    if event_path.exists():
        try:
            with event_path.open("ab"):
                pass
            event_writable = True
        except OSError:
            event_writable = False
        checks.append(
            _check(
                "event_log", "PASS" if event_writable and invalid_lines == 0 else "WARN",
                f"writable={event_writable}, events={len(mirror_events)}, invalid_lines={invalid_lines}",
                None if event_writable and invalid_lines == 0 else "SQLite remains authoritative; inspect or replace the damaged JSONL mirror after backup.",
            )
        )
    else:
        if db_event_ids:
            checks.append(
                _check(
                    "event_log", "WARN", "events.jsonl mirror is missing",
                    "SQLite remains authoritative; run a normal bus write to retry mirroring or regenerate the mirror after backup.",
                )
            )
        else:
            checks.append(
                _check("event_log", "PASS", "no event mirror is needed because SQLite has no events")
            )

    db_ids = set(db_event_ids)
    mirror_counts = Counter(mirror_ids)
    mirror_set = set(mirror_ids)
    db_only = sorted(db_ids - mirror_set)
    mirror_only = sorted(mirror_set - db_ids)
    duplicate_ids = sorted(event_id for event_id, count in mirror_counts.items() if count > 1)
    divergence = bool(db_only or mirror_only or duplicate_ids or invalid_lines)
    report["event_consistency"] = {
        "sqlite_event_count": len(db_ids),
        "mirror_event_count": len(mirror_ids),
        "archive_file_count": len(archive_paths),
        "sqlite_only_event_ids": db_only[:20],
        "mirror_only_event_ids": mirror_only[:20],
        "duplicate_mirror_event_ids": duplicate_ids[:20],
        "invalid_mirror_lines": invalid_lines,
    }
    checks.append(
        _check(
            "event_consistency", "WARN" if divergence else "PASS",
            "JSONL mirror diverges from authoritative SQLite" if divergence else "SQLite events and JSONL mirror IDs agree",
            "Preserve SQLite as source of truth; back up and regenerate the JSONL mirror." if divergence else None,
        )
    )

    if report["expired_leases"]:
        checks.append(
            _check(
                "expired_leases", "WARN", f"{len(report['expired_leases'])} leases are past due",
                "The assigned worker may reclaim them; run list/poll to materialize expiry.",
            )
        )
    else:
        checks.append(_check("expired_leases", "PASS", "no past-due active leases"))
    if report["stranded_tasks"]:
        checks.append(
            _check(
                "stranded_tasks", "WARN", f"{len(report['stranded_tasks'])} new tasks exceed {stranded_after_s}s",
                "Inspect the listed tasks; cancel only confirmed stale tasks as their original sender.",
            )
        )
    if report["possible_duplicates"]:
        checks.append(
            _check(
                "possible_duplicates", "WARN", f"{len(report['possible_duplicates'])} heuristic groups found",
                "These are possible, not proven, duplicates; compare intent before cancelling any task.",
            )
        )

    statuses = {check["status"] for check in checks}
    report["status"] = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return report
