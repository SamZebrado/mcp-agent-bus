# TODO

## Correctness / Reliability
- [ ] Add compact JSON schema validation for every tool argument
- [ ] Improve concurrency controls for multi-process access
- [ ] Add more edge case tests for state transitions

## MCP Compatibility
- [ ] Verify compatibility with additional MCP hosts beyond basic testing
- [ ] Add optional HTTP/SSE transport for hosts that do not support stdio servers

## SOLO Usage
- [ ] Add optional task heartbeat tool to extend leases without appending progress
- [ ] Add parent/child task relationships for multi-step delegation trees
- [ ] Add task result acceptance flow where planner can explicitly accept or reject done work

## Documentation
- [ ] Add detailed API reference for all bus methods
- [ ] Add more example workflows (3+ agents, sequential task chains, etc.)
- [ ] Add troubleshooting guide

## Scope / Safety
- [ ] Add per-agent authorization tokens if the bus is exposed outside a trusted local process boundary
- [ ] Add cleanup/export commands for archived tasks and event logs

## Future Features
- [ ] Support for task cancellation
- [ ] Support for task priorities and queue ordering beyond basic priority field
- [ ] Statistics and metrics about task completion rates, times, etc.

## Known Limitations
- Current implementation uses simple polling for wait operations (reasonable for local use)
- No built-in persistence for agent registration beyond last-seen time
- No support for task retries or retry limits
- No built-in notification mechanism beyond polling wait calls
- No ability to reassign tasks to different agents after they're sent

