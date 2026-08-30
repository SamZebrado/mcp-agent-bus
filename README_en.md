# mcp-agent-bus

> A small, local, auditable MCP task bus for delegating work between existing agent or IDE conversations.

MCP Agent Task Bus does not run agents or manage models and terminals. It provides a shared local task board: a planner sends work, workers claim it and return evidence, and the planner may explicitly review completed results.

## Architecture in 30 seconds

```text
planner MCP alias ─┐
worker-tests alias ├─ stdio MCP ─ SQLite (authoritative state and events)
worker-docs alias  ┘                 └─ events.jsonl (inspectable audit mirror)
```

- Python standard library only
- SQLite WAL for local multi-process access
- Task state and the authoritative `events` table commit together; `events.jsonl` is a post-commit best-effort mirror
- The MCP transport is currently **stdio only**; HTTP / Streamable HTTP is not implemented
- The optional localhost dashboard is read-only

This is not an agent runtime, tmux orchestrator, provider framework, or workflow engine.

## Verify in 30 seconds

```bash
git clone https://github.com/SamZebrado/mcp-agent-bus.git
cd mcp-agent-bus
python3 --version  # Python 3.10+
bash run_smoke.sh
```

The command runs a two-agent smoke, a real three-agent multi-process smoke, and the full unittest suite. It does not call an external AI service.

## SOLO: separate aliases, one shared data directory

Configure one MCP alias per active dialogue to avoid a host serializing long calls through one stdio server. Every alias must use the same absolute `MCP_AGENT_BUS_DATA_DIR`.

```json
{
  "mcpServers": {
    "agent-bus-planner": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/mcp-agent-bus/data"
      }
    },
    "agent-bus-worker-tests": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/mcp-agent-bus/data"
      }
    },
    "agent-bus-worker-docs": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/mcp-agent-bus/data"
      }
    }
  }
}
```

An alias is a host connection name. `agent_name` is the routing identity inside the bus:

| Dialogue | Alias | `agent_name` |
|---|---|---|
| planner | `agent-bus-planner` | `planner-main` |
| tests worker | `agent-bus-worker-tests` | `worker-tests` |
| docs worker | `agent-bus-worker-docs` | `worker-docs` |

Use `to="worker-tests"`, not the alias, when sending a task.

## Codex compact mode

For Codex, use one alias and prefer `codex_bus_sync` to combine register/send/claim/finish/watch/list and reduce MCP round trips. Atomic tools remain available for SOLO and TRAE.

```json
{
  "agent_name": "planner-main",
  "send": [{
    "to": "worker-docs",
    "body": "Check the minimal README path",
    "client_request_id": "readme-review-1"
  }],
  "compact": true
}
```

See [`docs/codex_mcp_config_example.toml`](docs/codex_mcp_config_example.toml).

## Doctor: diagnose before retrying

```bash
PYTHONPATH="$PWD" python3 -m mcp_agent_bus.cli --data-dir ./data doctor
```

`doctor` does not create a missing directory or database. It reports effective paths and read/write checks, SQLite integrity and schema, agents and task counts, expired leases, old `new` tasks, heuristic possible duplicates, recent tasks/events, SQLite-to-JSONL divergence, and actionable `PASS/WARN/FAIL` checks.

SQLite is authoritative if the JSONL mirror diverges. See [`docs/operations.md`](docs/operations.md).

## Minimal task flow

Planner:

1. `register_agent(agent_name="planner-main", role="planner")`
2. `send_task(to="worker-tests", body="Run tests", from_agent="planner-main", client_request_id="tests-1")`
3. `poll_for_result(task_id)`
4. Review a `done` result with `accept_task_result(...)` or `reject_task_result(...)`

Worker:

1. `register_agent(agent_name="worker-tests", role="worker")`
2. `poll_for_task(agent_name="worker-tests")`
3. Optionally call `append_progress(...)`
4. `finish_task(..., status="done", summary="...", evidence={...})`

`client_request_id` makes an ambiguous send retry safe: the same sender, key, and canonical payload return the original task; a changed payload fails. `cancel_task` lets only the original sender cancel an inactive `new/expired` task.

Run the three-agent example directly:

```bash
PYTHONPATH="$PWD" python3 scripts/smoke_three_agents.py
```

## Execution state and planner review

Execution states remain compatible:

```text
new → claimed → running → done / failed / blocked / rejected / cancelled / expired
```

Planner review is separate metadata: `NULL → accepted | rejected`. It does not replace `done` and is distinct from a worker execution result of `rejected`. An identical repeat is idempotent; a review cannot be silently flipped.

## More documentation

- [`docs/operations.md`](docs/operations.md): doctor, idempotency, cancellation, review, audit consistency, and recovery actions
- [`docs/three_agent_demo.md`](docs/three_agent_demo.md): planner + tests/docs workers
- [`docs/solo_two_dialogue_test_plan.md`](docs/solo_two_dialogue_test_plan.md): SOLO connectivity plan
- [`docs/dashboard.md`](docs/dashboard.md): read-only dashboard
- [`SCOPE.md`](SCOPE.md): product boundary
- [`TODO.md`](TODO.md): remaining non-blocking work
