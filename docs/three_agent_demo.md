# Three-agent demo

This demo models three existing conversations/processes:

```text
planner-main
  ├─ sends independent test task ─→ worker-tests
  └─ sends independent docs task ─→ worker-docs

workers claim and finish in separate processes
planner aggregates both done results and accepts them
```

Run it:

```bash
PYTHONPATH="$PWD" python3 scripts/smoke_three_agents.py
```

The script creates a temporary shared data directory, starts two worker processes, verifies both results, records planner acceptance, parses every JSONL event, runs doctor, prints evidence, and removes the temporary directory.

Expected shape:

```text
MULTI_AGENT_SMOKE OK
tasks=task_...,task_...
workers=worker-tests,worker-docs
reviews=accepted,accepted
events=13
doctor=PASS
```

The task IDs vary. The test does not call external AI.

## SOLO mapping

Use three MCP aliases with one absolute data directory:

| Dialogue | Alias | Registered identity |
|---|---|---|
| Planner | `agent-bus-planner` | `planner-main` |
| Tests worker | `agent-bus-worker-tests` | `worker-tests` |
| Docs worker | `agent-bus-worker-docs` | `worker-docs` |

Suggested planner prompt:

> Use `agent-bus-planner` and register as `planner-main`. Send one independent task each to `worker-tests` and `worker-docs`, using a stable `client_request_id` for each intent. Poll the two task IDs, aggregate evidence, then explicitly accept or reject each done result.

Suggested tests-worker prompt:

> Use `agent-bus-worker-tests` and register as `worker-tests`. Poll one task, perform only the requested tests, and finish with a concise summary plus exact command evidence.

Suggested docs-worker prompt:

> Use `agent-bus-worker-docs` and register as `worker-docs`. Poll one task, perform only the requested documentation check, and finish with a concise summary plus file evidence.

## What the demo proves

- distinct processes share one SQLite WAL store;
- routing uses `agent_name`, not MCP alias;
- each worker owns only its claimed task;
- planner review remains separate from `done`;
- the JSONL mirror is parseable and doctor sees no divergence.

Race safety is covered separately by unittest: simultaneous same-key sends produce one task, and simultaneous claims produce exactly one winner.
