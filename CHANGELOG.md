## Unreleased

- Added provider-neutral `bus_sync` as the preferred compact coordination tool; `codex_bus_sync` remains a compatibility alias.
- Added a deterministic two-host MCP stdio/JSON-RPC E2E test covering send, idempotent retry, claim, progress, finish, result, planner review, and doctor.
- Hardened concurrent first-start SQLite WAL initialization so independent MCP hosts can start against the same fresh data directory without a transient lock failure.
- Clarified the project boundary: Codex-only workflows should prefer native Codex coordination; this project focuses on provider/host-neutral persistent local coordination.

# Changelog

## v0.2.0 - 2026-08-30

- Added `doctor` CLI/MCP diagnostics for effective paths, permissions, SQLite integrity/schema, agents, task counts, lease expiry, stranded tasks, possible duplicates, recent records, and audit-mirror divergence
- Made SQLite `events` the authoritative transactional audit source and documented `events.jsonl` as a best-effort post-commit mirror
- Added `client_request_id` send idempotency with canonical payload checks and concurrent retry protection
- Added sender-only `cancel_task` for inactive `new/expired` tasks
- Added immutable planner `accept_task_result` / `reject_task_result` metadata without changing existing `done` semantics
- Added a planner + tests/docs worker multi-process smoke and deterministic send/claim race tests
- Reworked README / README_en and the reusable Skill around install, doctor, aliases, connectivity, multi-worker setup, and actionable diagnosis
- Added `codex_bus_sync` as a compact workflow tool for Codex-oriented MCP usage
- Added MCP initialize instructions recommending `codex_bus_sync` to reduce redundant MCP round trips and context overhead
- Added tests covering compact workflow send/claim/finish/watch behavior and compact payload shape
- Added README / README_en compact mode documentation and minimal Codex MCP configuration example

## v0.1.0 - Initial MVP

- Added local MCP Agent Task Bus
- Added SQLite-backed task state
- Added append-only JSONL event log for audit trail
- Added MCP stdio server exposing task delegation tools
- Added CLI commands for manual testing and inspection
- Added blocking wait tools: `wait_for_task`, `wait_for_result`
- Added non-blocking polling tools: `poll_for_task`, `poll_for_result`
- Added unit tests and smoke test
- Added SOLO multi-dialogue usage documentation
- Verified manual SOLO two-dialogue task handoff
- Verified dual MCP server alias real-time communication
