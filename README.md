# mcp-agent-bus

> 轻量、本地、可审计的 MCP 任务总线，让已有 agent / IDE 对话互相派任务。

MCP Agent Task Bus 不运行 agent，也不管理模型或终端。它只提供一块共享的本地任务板：planner 发送任务，worker 领取并返回证据，planner 可显式验收结果。

## 当前定位

Codex 当前已经提供原生的多 agent 与跨 task/thread 协作能力。**如果工作完全发生在 Codex 内部，应优先使用 Codex 原生能力**，无需为了同类编排再额外引入本项目。

`mcp-agent-bus` 当前保留的价值是一个 **provider-neutral、host-neutral 的本地协调层**：当多个彼此独立的 MCP host/runtime 需要共享任务状态时，它提供统一的 stdio 工具契约、持久 SQLite 状态、幂等发送、lease ownership、显式结果验收和可审计事件记录。这些状态不依赖某个特定 AI 产品自己的 thread/session 生命周期。

项目因此进入“稳定工具”定位：优先保证兼容性、可靠性和可测试性，不主动扩展成通用 agent runtime。

## 30 秒架构

```text
planner MCP alias ─┐
worker-tests alias ├─ stdio MCP ─ SQLite（权威状态与事件）
worker-docs alias  ┘                 └─ events.jsonl（可检查的审计 mirror）
```

- Python 标准库，无运行时依赖
- SQLite WAL 支持本地多进程访问
- `events` 表与任务状态同事务提交；`events.jsonl` 是提交后的 best-effort mirror
- MCP transport 当前仅支持 **stdio**；HTTP / Streamable HTTP 未实现
- Dashboard 只读，默认仅绑定 `127.0.0.1`

这不是 agent runtime、tmux orchestrator、LLM provider framework 或 workflow engine。

## 30 秒验证

```bash
git clone https://github.com/SamZebrado/mcp-agent-bus.git
cd mcp-agent-bus
python3 --version  # 需要 Python 3.10+
bash run_smoke.sh
```

`run_smoke.sh` 会运行双 agent smoke、planner + 两个 worker 的多进程 smoke，以及完整 unittest；其中包含两个独立假 MCP host 通过真实 stdio/JSON-RPC 完成 send → claim → progress → finish → result → review 的端到端测试。不会调用任何外部 AI。

## SOLO：多个 alias，共享一个 data dir

为每个活跃对话配置独立 MCP alias，避免某些 host 把同一个 stdio server 的长调用串行化。所有 alias 必须共享同一个绝对 `MCP_AGENT_BUS_DATA_DIR`。

```json
{
  "mcpServers": {
    "agent-bus-planner": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/mcp-agent-bus/data"
      }
    },
    "agent-bus-worker-tests": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/mcp-agent-bus/data"
      }
    },
    "agent-bus-worker-docs": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/mcp-agent-bus/data"
      }
    }
  }
}
```

alias 是 MCP host 的连接名；`agent_name` 才是总线内的路由身份：

| 对话 | alias | `agent_name` |
|---|---|---|
| planner | `agent-bus-planner` | `planner-main` |
| tests worker | `agent-bus-worker-tests` | `worker-tests` |
| docs worker | `agent-bus-worker-docs` | `worker-docs` |

发送时 `to="worker-tests"`，不要填 alias。

## Compact sync

对任何兼容 MCP host，都可以优先使用 provider-neutral 的 `bus_sync` 合并 register/send/claim/finish/watch/list，减少 MCP round trips。

旧的 `codex_bus_sync` 名称继续保留为兼容别名；已有配置无需迁移。Codex-only 工作流本身应优先考虑 Codex 原生跨 task/thread 协作，而不是为了相同用途额外增加一层 bus。

```json
{
  "agent_name": "planner-main",
  "send": [{
    "to": "worker-docs",
    "body": "检查 README 的最小上手路径",
    "client_request_id": "readme-review-1"
  }],
  "compact": true
}
```

现有 Codex TOML 示例仍见 [`docs/codex_mcp_config_example.toml`](docs/codex_mcp_config_example.toml)；其他 MCP host 只需启动同一个 stdio server，并让独立连接共享同一个绝对 `MCP_AGENT_BUS_DATA_DIR`。

## Doctor：先诊断，再重试

```bash
PYTHONPATH="$PWD" python3 -m mcp_agent_bus.cli --data-dir ./data doctor
```

`doctor` 默认不创建缺失的数据目录或数据库。它报告：

- 实际 data dir、SQLite、`events.jsonl` 路径与读写能力
- SQLite integrity、schema version、agents、任务状态计数
- 已过期 lease、长期 `new` 任务、启发式 possible duplicates
- 最近任务/事件，以及 SQLite 权威事件与 JSONL mirror 的差异
- 总体 `PASS/WARN/FAIL` 和下一步动作

JSONL 与 SQLite 分歧时，以 SQLite 为准；不要把 mirror 当成事务权威源。排障说明见 [`docs/operations.md`](docs/operations.md)。

## 最小任务流

Planner：

1. `register_agent(agent_name="planner-main", role="planner")`
2. `send_task(to="worker-tests", body="运行测试", from_agent="planner-main", client_request_id="tests-1")`
3. `poll_for_result(task_id)`
4. 对 `done` 结果调用 `accept_task_result(...)` 或 `reject_task_result(...)`

Worker：

1. `register_agent(agent_name="worker-tests", role="worker")`
2. `poll_for_task(agent_name="worker-tests")`
3. 可选 `append_progress(...)`
4. `finish_task(..., status="done", summary="...", evidence={...})`

`client_request_id` 用于 planner 在 MCP timeout 后安全重试：同 sender + 同 key + 同 payload 返回原 task；同 key 不同 payload 明确失败。`cancel_task` 只允许原 sender 取消尚未活跃的 `new/expired` 任务。

运行真实三 agent 本地示例：

```bash
PYTHONPATH="$PWD" python3 scripts/smoke_three_agents.py
```

## 状态与验收

执行状态保持兼容：

```text
new → claimed → running → done / failed / blocked / rejected / cancelled / expired
```

Planner 验收是独立 metadata：`NULL → accepted | rejected`。它不改变 worker 的 `done`，也不等同于 worker 执行结果中的 `rejected`。首次相同复审幂等，验收结果不可静默翻转。

## 更多文档

- [`docs/operations.md`](docs/operations.md)：doctor、幂等、取消、验收、事件一致性与故障动作
- [`docs/three_agent_demo.md`](docs/three_agent_demo.md)：planner + tests/docs workers 示例
- [`docs/solo_two_dialogue_test_plan.md`](docs/solo_two_dialogue_test_plan.md)：SOLO 双对话联通计划
- [`docs/dashboard.md`](docs/dashboard.md)：只读 dashboard
- [`SCOPE.md`](SCOPE.md)：项目边界
- [`TODO.md`](TODO.md)：剩余非阻塞工作
