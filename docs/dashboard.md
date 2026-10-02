# Read-only Local Dashboard

The dashboard is an optional observability component for `mcp-agent-bus`.
It is not an orchestration engine.

Properties:

- It does not start with the MCP server.
- The MCP server does not import or depend on the dashboard.
- It is manually started by the user.
- It is read-only in v1.
- It binds to `127.0.0.1` by default.
- It observes agents, tasks, progress, and event timelines from an existing local task-board data directory.

Start:

```bash
python3 -m mcp_agent_bus.dashboard --data-dir ./data --host 127.0.0.1 --port 8765
```

If installed as a package:

```bash
mcp-agent-bus-dashboard --data-dir ./data --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

The dashboard reads:

- `mcp_agent_bus.sqlite`
- the `events.jsonl` audit mirror for event timelines

SQLite is the authoritative task/event source. If `doctor` reports JSONL divergence,
the dashboard task state remains SQLite-backed but its event timeline may be incomplete
until the mirror is repaired.

It does not:

- create tasks
- claim tasks
- finish, cancel, or reject tasks
- trigger agents
- execute shell commands
- modify source code or git state
- modify task-board state

Current limitations:

- No human mode.
- No write operations.
- No workflow drag-and-drop.
- No permissions or login system.
- No WebSocket or realtime push; pages use manual refresh plus lightweight browser refresh.

Future work may add a separate, explicitly enabled human review mode, but that is outside v1.


## Network boundary

The dashboard checks the HTTP Host authority before reading task data, including on detail and health routes, to reject DNS-rebinding hostnames. The default loopback binding accepts only `127.0.0.1` and `localhost` with the configured port. An explicit external `--host` accepts that configured host; a wildcard `0.0.0.0` bind additionally accepts the connection's destination IP, not arbitrary DNS names.

This check is not authentication. External binding can disclose task bodies, evidence, events and raw metadata to clients that can reach the port. Keep the default loopback binding, or restrict explicit external access with a trusted network/firewall. Reverse proxies with different Host names are rejected.
