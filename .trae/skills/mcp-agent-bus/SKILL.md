---
name: mcp-agent-bus
description: Diagnose, configure, and use a local stdio MCP task bus for auditable SOLO multi-dialogue handoffs.
---

# MCP Agent Bus Skill

Use this Skill when a user wants existing SOLO/IDE conversations to delegate focused tasks through `mcp-agent-bus`. Keep the bus small: do not turn it into an agent runtime, terminal launcher, provider abstraction, or workflow engine.

## Workflow

1. **Locate and verify**
   - Resolve the project and shared data directory to absolute paths.
   - Confirm Python 3.10+ and the files `mcp_agent_bus/server.py` and `run_smoke.sh`.
   - Run `bash run_smoke.sh` for a new installation. Report the real result.

2. **Run doctor before configuration or retry**

   ```bash
   PYTHONPATH="/absolute/mcp-agent-bus" \
   python3 -m mcp_agent_bus.cli \
     --data-dir "/absolute/shared/data" doctor
   ```

   Do not create or replace a missing store merely to make doctor pass. Follow its `PASS/WARN/FAIL` actions. SQLite is authoritative; `events.jsonl` is a best-effort audit mirror.

3. **Choose identities**
   - Planner: `planner-main`
   - Workers: short role names such as `worker-tests`, `worker-docs`
   - Explain that the MCP alias is a connection name; `agent_name` is the task-routing identity.

4. **Generate aliases**
   - Use one stdio alias per active SOLO dialogue.
   - Point every alias at `python3 -m mcp_agent_bus.server`.
   - Give every alias the same absolute `PYTHONPATH` and `MCP_AGENT_BUS_DATA_DIR`.
   - Do not claim HTTP / Streamable HTTP support.

5. **Run a minimal connectivity check**
   - Planner registers and sends to one worker with `from_agent` and a stable `client_request_id`.
   - Worker registers, polls, claims, and finishes with evidence.
   - Planner polls the exact task ID and accepts or rejects a `done` result.
   - Prefer polling when the host may serialize calls on one stdio connection.

6. **Expand to multiple workers only after the minimal check passes**
   - Add one alias and `agent_name` per worker role.
   - Send independent task IDs; do not invent a workflow graph.
   - Offer `scripts/smoke_three_agents.py` as the local no-AI proof.

7. **Diagnose failures**
   - Ambiguous send timeout: retry once with the same `client_request_id` and identical payload.
   - Possible duplicate `new` tasks: inspect first; only the original sender may cancel a confirmed stale `new/expired` task.
   - Expired lease: let the assigned worker reclaim it.
   - Wrong worker: compare `to` with registered `agent_name`, not alias.
   - JSONL divergence: preserve SQLite and the data directory; do not infer state from JSONL alone.
   - Database locked/integrity failure: stop broad retries and follow doctor actions.

## Configuration template

Generate entries from the user's real absolute paths:

```json
{
  "mcpServers": {
    "agent-bus-planner": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/absolute/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/absolute/shared/data"
      }
    },
    "agent-bus-worker-tests": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/absolute/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/absolute/shared/data"
      }
    }
  }
}
```

## Prompt templates

Planner:

> Use `<planner-alias>` and register as `<planner-agent-name>`. Send a focused task to `<worker-agent-name>` with a stable `client_request_id`. Keep the task ID, poll that exact result, inspect evidence, then accept or reject a done result.

Worker:

> Use `<worker-alias>` and register as `<worker-agent-name>`. Poll one assigned task, do only that task, and finish with a concise summary plus exact evidence. Do not claim or finish work addressed to another agent name.

## Output requirements

- Report verified, warning, and unverified states separately.
- Provide the exact data directory, alias-to-agent mapping, minimal check, and recovery action.
- Keep prompts short and role-specific.
- Do not modify the user's MCP settings or runtime data unless explicitly asked.
