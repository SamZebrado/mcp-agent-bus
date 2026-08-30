# Project Scope

This project is a small local MCP task bus for handoff, status synchronization, result delivery, planner review, and auditability between existing compatible agent conversations or tools.

## What This Project Does

- Stores task state and authoritative append-only events in local SQLite WAL
- Mirrors committed events to inspectable `events.jsonl` on a best-effort basis
- Exposes an MCP **stdio** server and a local CLI
- Provides a non-creating `doctor` inspection path with actionable `PASS/WARN/FAIL`
- Supports:
  - agent registration and last-seen state
  - directed task send with acceptance criteria, priority, timeout, and optional idempotency key
  - leased claim, progress, worker ownership, result, bounded wait, and polling
  - safe sender-only cancellation of inactive `new/expired` tasks
  - planner-only acceptance/rejection metadata for `done` results
  - compact Codex calls while retaining atomic SOLO/TRAE tools
  - a read-only localhost dashboard

## What This Project Is For

- Splitting focused work across SOLO or compatible MCP dialogues
- Letting a planner delegate independent tasks to named workers
- Making ambiguous send retries safe with `client_request_id`
- Keeping local task state, evidence, review, and events inspectable
- Coordinating existing conversations without running or spawning them

## What This Project Does Not Do

- Does not run, spawn, schedule, or supervise agents or terminals
- Does not provide an LLM provider abstraction or execute task bodies
- Does not implement workflow graphs, parent/child orchestration, or automatic reassignment
- Does not provide generic agent chat/inbox semantics or file reservations
- Does not expose writable dashboard controls
- Does not implement HTTP, Streamable HTTP, SSE, WebSocket, or cloud deployment
- Does not provide network authentication; it is designed for a trusted local process boundary
- Does not bypass host sandbox, approval, or safety restrictions

## State and review boundary

Worker execution uses the existing task states:

`new`, `claimed`, `running`, `blocked`, `done`, `failed`, `rejected`, `cancelled`, `expired`.

Planner result review is separate metadata (`NULL`, `accepted`, `rejected`) and never changes `done`. It does not reopen or reassign rejected results.

## Data Storage

The default `./data` directory may be overridden by `MCP_AGENT_BUS_DATA_DIR` and contains:

- `mcp_agent_bus.sqlite`: authoritative tasks, agents, progress, review, and events
- SQLite WAL/SHM files while active
- `events.jsonl`: post-commit audit mirror
- optional `event_archives/*.jsonl` created by event-log cleanup

SQLite and a filesystem log cannot commit atomically. `doctor` detects mirror divergence; SQLite remains authoritative.

## Agent Compatibility

Agents need an MCP host that supports stdio tools. Multiple aliases may share one absolute data directory. Workers should return concise evidence such as commands, files, or test results.
