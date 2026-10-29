# TODO

## Optional standalone tools

- [x] Self-contained `light/` folder and separate console entry, using one source tree
- [x] Offline privacy/approval/budget/unknown-outcome tests and isolated wheel checks
- [x] Non-echo, process-only key input for separately approved terminal chat
- [ ] Verify actual host per-call approval UI before any live MCP use
- [ ] Explicitly authorized tiny live API test and independent Platform usage reconciliation
- [ ] Standalone ACP-to-MCP adapter: separate folder/package, no task-queue coupling,
      no agent loop, no implicit shell/filesystem permission, explicit attribution
- [ ] Real Harness/ACP wire/version/cancel/budget verification; current mock is not support

No automatic live test, credentials setup, paid API usage, or plugin/package
publication follows from these TODOs.

## Correctness / Reliability

- [x] Add multi-process transaction coverage for same-task claims and same-key sends
- [x] Make authoritative audit events transactional with task state
- [x] Add idempotent send, safe inactive-task cancellation, and immutable planner review
- [ ] Add compact runtime JSON-schema validation for every tool argument
- [ ] Add an explicit backup + regenerate command for the derived JSONL event mirror
- [ ] Add bounded stress coverage on Windows in addition to current local multi-process tests

## MCP Compatibility

- [x] Keep stdio transport explicit in README and diagnostics
- [x] Add provider-neutral two-host stdio/JSON-RPC E2E coverage with deterministic fake hosts
- [ ] Verify a real additional MCP host only when a concrete integration needs it
- [ ] Write a design-only note for optional Streamable HTTP, including authentication and threat-model costs

HTTP implementation is intentionally not prioritized while the project remains local and small.

## SOLO Usage

- [x] Add a real planner + worker-tests + worker-docs multi-process demo
- [x] Add planner result acceptance/rejection without changing `done`
- [ ] Consider a minimal lease heartbeat only if real usage shows progress events are insufficient

## Documentation / Skill

- [x] Add doctor troubleshooting actions
- [x] Turn the Skill into an installation → doctor → alias → connectivity → multi-worker workflow
- [x] Keep planner/worker prompts short and role-specific
- [ ] Add a compact generated API reference if host/tool discovery proves insufficient

## Explicitly deferred

- Parent/child workflow graphs
- Generic inbox/message threads
- File reservations
- Agent/provider runtime
- tmux/session launcher
- Writable dashboard
- Cloud deployment / WebSocket

## Known Limitations

- Wait operations use simple bounded polling, appropriate for local use.
- Agent registration persists identity and last-seen timestamps, not a live process guarantee.
- JSONL is a best-effort mirror, not a second transactional source of truth.
- Planner rejection records review only; it does not reopen or reassign work.
- No built-in notification mechanism exists beyond wait/poll calls.
- No automatic reassignment or retry limit is implemented.

