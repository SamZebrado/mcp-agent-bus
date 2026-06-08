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
- `events.jsonl`

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
