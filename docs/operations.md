# Reliability and operations

## Source of truth

SQLite is authoritative for both current task state and the append-only `events` table. Each state change and its event commit in one SQLite transaction.

`events.jsonl` is a human-inspectable, post-commit mirror. Mirror writes are serialized through SQLite and use stable `event_id` values, but SQLite and the filesystem cannot form one atomic transaction. A crash or filesystem error may therefore leave a missing, duplicate, malformed, or extra JSONL line. The task operation remains committed if SQLite committed.

`doctor` compares event IDs across SQLite, active `events.jsonl`, and archived JSONL files. If they disagree, preserve SQLite and the data directory before repairing or regenerating the mirror. Do not infer task state from JSONL alone.

## Doctor

Run without installing the package:

```bash
PYTHONPATH="$PWD" python3 -m mcp_agent_bus.cli --data-dir /absolute/shared/data doctor
```

Or, after installation:

```bash
mcp-agent-bus --data-dir /absolute/shared/data doctor
```

Useful options:

```bash
mcp-agent-bus --data-dir /absolute/shared/data doctor \
  --recent-limit 10 \
  --stranded-after-s 7200
```

The CLI inspection path does not create a missing directory/database and does not run schema migration. Its temporary directory write probe is created and immediately removed; its SQLite writeability probe acquires and rolls back a bounded immediate transaction without changing rows.

The MCP `doctor` tool inspects the already-running server's initialized store. Use the CLI when the server itself cannot start or the configured path may be wrong.

Interpretation:

- `PASS`: all required checks succeeded and no warning heuristic fired.
- `WARN`: the bus may still operate, but a lease, stranded task, possible duplicate, schema drift, or audit-mirror issue needs inspection.
- `FAIL`: path, permission, SQLite integrity, write-lock, or required-schema checks prevent a trustworthy run.

Possible duplicates are heuristic groups of `new/expired` tasks with the same sender, assignee, and body. They are not automatically cancelled.

## Safe send retry

Use a stable `client_request_id` per planner intent:

```text
send_task(
  to="worker-tests",
  body="Run the focused test suite",
  from_agent="planner-main",
  client_request_id="focused-tests-2026-08-30"
)
```

The key is scoped to `from_agent`. The canonical payload includes:

- `to`
- `body`
- `acceptance_criteria` (canonical JSON; list order is preserved)
- normalized `priority` (`None` and `0` are equivalent)
- `timeout_s`
- `from_agent`

Concurrent retries with the same key and payload return one task. Reusing the key with a different canonical payload fails and does not add another `task_sent` event. A key requires a non-empty `from_agent`.

## Cancellation

`cancel_task(task_id, agent_name, reason?)` is deliberately narrow:

- `agent_name` must equal the task's original `from_agent`;
- only `new` or `expired` tasks can be cancelled;
- claimed/running/blocked/finished work cannot be invalidated behind a worker's back;
- cancelling an expired task clears stale claim and lease fields;
- an identical repeat by the sender returns the already-cancelled task without a second event.

Inspect possible duplicates first. Do not cancel merely because task bodies match.

## Planner result review

Worker execution state and planner review are separate:

```text
worker:  ... → done
planner: result_review NULL → accepted | rejected
```

Only a `done` task with a non-null original sender is reviewable. Only that sender may call `accept_task_result` or `reject_task_result`. An identical repeated review is idempotent; changing the decision or note is rejected. Rejection does not reopen, reassign, or alter `done`.

The existing worker execution status `rejected` remains unchanged and is not planner review.

## Common actions

| Doctor finding | Action |
|---|---|
| data dir / SQLite missing | Verify the effective absolute path. Initialize only after confirming it is intended. |
| schema behind current version | Back up the data directory, then start the current bus once for additive migration. |
| expired lease | The assigned worker may reclaim it; poll/list materializes expiry. |
| old `new` task | Confirm whether a worker alias/`agent_name` is wrong; cancel only as original sender if stale. |
| possible duplicates | Compare task intent and IDs; adopt `client_request_id` for future sends. |
| JSONL divergence | Keep SQLite, preserve the directory, and regenerate/repair the mirror from authoritative events. |
| SQLite integrity failure | Stop writers and recover from a verified backup before continuing. |
| database locked | Check for a long-running writer; do not delete WAL/SHM files from a live store. |

## Transport boundary

The implemented MCP transport is stdio. Multiple aliases are multiple stdio server processes sharing one local SQLite data directory. HTTP / Streamable HTTP is a future design option, not a hidden or partially supported mode. Exposing the local data store over a network would also require authentication and a larger threat model, so it is intentionally out of scope for this release.
