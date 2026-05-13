# mcp-agent-bus

This tool lets multiple SOLO dialogues or compatible MCP agent tools communicate through a local task bus. A planner agent can delegate tasks to worker agents, wait for results, and all task states, progress, and evidence are stored locally with an auditable append-only event log.

This project is intentionally minimal:

- Python standard library only (no external dependencies)
- SQLite for current state
- Append-only `data/events.jsonl` for audit history
- MCP stdio server exposing task delegation tools
- CLI for manual testing and inspection
- Smoke tests that do not call external AI services

## Why This Project Exists

When working on complex tasks with SOLO, it can be useful to split work across multiple specialized dialogues:
- A main "planner" dialogue handles high-level planning and coordination
- Specialized "worker" dialogues handle discrete, focused tasks (testing, documentation, refactoring, etc.)
- This avoids overwhelming a single dialogue with too much context or too many responsibilities

This task bus provides a structured way to coordinate these dialogues locally without external services.

## What You Can Do With It

- Delegate tasks from one agent to another
- Track task states from new → claimed → running → done/failed
- Attach progress updates and evidence to tasks
- Wait for tasks or results with bounded timeouts
- Maintain an auditable record of all task activity

## Tools

The MCP server exposes these tools:

- `register_agent(agent_name, role?)`: Register or refresh a local agent identity
- `send_task(to, body, acceptance_criteria?, priority?, timeout_s?, from_agent?)`: Create a task for another agent
- `wait_for_task(agent_name, max_wait_s?, lease_s?)`: Blocking bounded wait for the next task, then claim it with a lease (returns `{"status": "timeout"}` if no task arrives in time)
- `poll_for_task(agent_name, lease_s?)`: Non-blocking check for the next task; claim it if available (returns `{"status": "ok", "task": ...}` if task found, `{"status": "empty", "task": null}` otherwise)
- `claim_task(task_id, agent_name, lease_s?)`: Claim a specific new or expired task
- `append_progress(task_id, agent_name, message, evidence?)`: Append progress and evidence; moves `claimed` → `running`
- `finish_task(task_id, agent_name, status, summary, changed_files?, evidence?, error_message?)`: Mark task as done/failed/blocked/rejected/cancelled
- `wait_for_result(task_id, max_wait_s?)`: Blocking bounded wait for task to reach terminal state (returns `{"status": "timeout"}` if no result in time)
- `poll_for_result(task_id)`: Non-blocking check for task's result; returns `{"status": "ok", "task": ...}` if terminal, `{"status": "pending", "task": ...}` otherwise
- `get_task(task_id)`: Get full task including progress
- `list_tasks(filter?)`: List tasks with optional `to`, `status`, or `limit` filters

## Blocking vs Polling Modes

For SOLO multi-dialogue usage, some MCP hosts may serialize tool calls to the same stdio server, which can cause long `wait_for_task` / `wait_for_result` calls to block other dialogues.

Recommended approaches:
1. **Two separate MCP servers**: Configure two different MCP server names (e.g., `agent-bus-planner` and `agent-bus-worker`), both pointing to the same executable and sharing the same `MCP_AGENT_BUS_DATA_DIR`.
2. **Polling mode**: Use `poll_for_task` and `poll_for_result` instead of the blocking variants, which return immediately without waiting.

The blocking `wait_for_task` / `wait_for_result` are still available and recommended for environments that support stable blocking tool calls.

## Task States

`new`, `claimed`, `running`, `blocked`, `done`, `failed`, `rejected`, `cancelled`, `expired`

## Install / Run

From this directory:

### Run MCP Server
```bash
PYTHONPATH="$PWD" python3 -m mcp_agent_bus.server
```

### Use CLI
```bash
# Register agents
PYTHONPATH="$PWD" python3 -m mcp_agent_bus register-agent planner --role planner
PYTHONPATH="$PWD" python3 -m mcp_agent_bus register-agent worker --role worker

# Send a task
PYTHONPATH="$PWD" python3 -m mcp_agent_bus send-task worker "Write unit tests for the new feature" --from-agent planner --priority 5

# Worker waits for and claims a task
PYTHONPATH="$PWD" python3 -m mcp_agent_bus wait-for-task worker --max-wait-s 10 --lease-s 300
```

### Run Smoke Test
```bash
bash run_smoke.sh
```

## Data Storage Configuration

By default, all data is stored in `./data`. Override with the `MCP_AGENT_BUS_DATA_DIR` environment variable:
```bash
MCP_AGENT_BUS_DATA_DIR=/tmp/my-agent-bus PYTHONPATH="$PWD" python3 -m mcp_agent_bus.server
```

Data includes:
- SQLite database: `mcp_agent_bus.sqlite` (current state)
- Append-only event log: `events.jsonl` (audit history)

## MCP Host Configuration

Add to your MCP host's configuration (adjust paths as needed):

```json
{
  "mcpServers": {
    "agent-bus": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus/data"
      }
    }
  }
}
```

## SOLO Multi-Dialogue Recommended Setup

For SOLO multi-dialogue usage, it is recommended to configure a separate MCP server alias for each active SOLO dialogue or role. All aliases should point to the same `mcp_agent_bus.server` executable and share the same `MCP_AGENT_BUS_DATA_DIR`.

**Important distinction**:
- **MCP server alias**: The key in the `mcpServers` configuration object (e.g., `agent-bus-planner`, `agent-bus-worker`). This determines how SOLO identifies and communicates with this server.
- **agent_name**: The identifier registered via `register_agent()`, which determines task routing within the bus (e.g., `"planner-main"`, `"worker-docs"`, `"worker-tests"`).

Different MCP server aliases sharing the same `MCP_AGENT_BUS_DATA_DIR` can communicate with each other in real time, which avoids issues where certain MCP hosts may serialize tool calls to the same stdio server alias.

### Recommended Mapping

| SOLO Dialogue | MCP Server Alias | agent_name |
|---------------|------------------|------------|
| Main/Planner | `agent-bus-planner` | `planner-main` |
| Worker (docs) | `agent-bus-worker-docs` | `worker-docs` |
| Worker (tests) | `agent-bus-worker-tests` | `worker-tests` |
| Worker (review) | `agent-bus-worker-review` | `worker-review` |

**Important**: When calling `send_task()`, use the `agent_name` as the `to` parameter, not the MCP server alias. For example: `send_task(to="worker-docs", ...)` sends to the worker-docs agent, not to the agent-bus-worker-docs server.

### Example: Multi-Dialogue Configuration

```json
{
  "mcpServers": {
    "agent-bus-planner": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus/data"
      }
    },
    "agent-bus-worker-docs": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus/data"
      }
    },
    "agent-bus-worker-tests": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/Users/samzebrado/Documents/PersonalCodingLocal/mcp-agent-bus/data"
      }
    }
  }
}
```

In this setup:
- `agent-bus-planner` is used by the main "planner" SOLO dialogue, which registers as `planner-main`
- `agent-bus-worker-docs` is used by the docs worker SOLO dialogue, which registers as `worker-docs`
- `agent-bus-worker-tests` is used by the tests worker SOLO dialogue, which registers as `worker-tests`

All dialogues share the same SQLite database and event log for real-time task coordination.

## Example Two-Agent Workflow

### Planner Dialogue
1. Call `register_agent("planner")`
2. Call `send_task(to="worker", body="Write a README draft", from_agent="planner")` and note the `task_id`
3. Later, call `wait_for_result(task_id, max_wait_s=300)` to check for the result

### Worker Dialogue
1. Call `register_agent("worker")`
2. Call `wait_for_task("worker", max_wait_s=60)` to get the next task
3. Do the work
4. Optionally call `append_progress(task_id, "worker", "In progress...")`
5. Call `finish_task(task_id, "worker", "done", "README draft completed", evidence={"file": "README.md"})`

## Lease Behavior

When a task is claimed, it gets a time-limited lease. If the lease expires before the task is finished, the task will be marked as `expired` and becomes claimable again by the same worker. This gives visibility into timeouts while still allowing retries.

## Project Scope & Limitations

For details about what this project does and does not do, see [SCOPE.md](./SCOPE.md).

For future plans and known limitations, see [TODO.md](./TODO.md).

