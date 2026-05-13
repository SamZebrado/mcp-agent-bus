# Project Scope

This project is a local MCP Agent Task Bus designed to facilitate task handoff, status synchronization, result delivery, and audit logging between compatible MCP agents or tools.

## What This Project Does

- Provides a local SQLite-backed task storage layer with append-only event logging for auditability
- Exposes an MCP stdio server that can be integrated into compatible MCP hosts
- Offers a CLI for manual testing and inspection
- Supports:
  - Registering agent identities with optional roles
  - Sending tasks to specific agents with acceptance criteria, priorities, and timeouts
  - Claiming tasks with time-limited leases
  - Appending progress updates with evidence
  - Marking tasks as done/failed/blocked/rejected/cancelled
  - Waiting for new tasks (bounded)
  - Waiting for task results (bounded)
  - Listing and inspecting tasks
  - Lease expiration that makes timed-out tasks claimable again

## What This Project Is For

- Enabling multiple SOLO dialogues to split up work and hand off tasks locally
- Letting a main "planner" agent delegate discrete tasks to specialized "worker" agents
- Maintaining an auditable trail of task state changes and evidence
- Coordinating work without requiring external services or network access

## What This Project Does Not Do

- Does not control arbitrary AI tools or bypass any restrictions
- Does not provide free compute or agent capacity
- Does not automatically complete tasks; tasks are executed by the agents that claim them
- Does not replace human judgment; it is still recommended for users or the primary agent to review and accept results
- Does not guarantee any particular outcome or success rate
- Does not currently support multi-step delegation trees, explicit acceptance/rejection flows, or optional HTTP/SSE transports (see TODO.md for future plans)
- Does not implement per-agent authorization tokens for remote access (designed for local use only)

## Data Storage

- By default, all data is stored locally in the `./data` directory (can be configured via `MCP_AGENT_BUS_DATA_DIR` environment variable)
- Data includes:
  - SQLite database (`mcp_agent_bus.sqlite`) for current task and agent state
  - Append-only JSONL event log (`events.jsonl`) for audit history

## Agent Compatibility

- Agents must support MCP and be able to call the tools exposed by this server
- Worker agents should include relevant evidence when finishing tasks (e.g., modified files, command outputs, test results, error details)
