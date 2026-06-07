# Changelog

## Unreleased

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
