# mcp-agent-bus

> A local MCP task bus for auditable SOLO multi-dialogue handoffs and compatible MCP agent tools.

MCP Agent Task Bus 是一个本地 MCP 任务总线，用于让 SOLO 多对话或兼容 MCP 的 agent 工具进行可审计任务接力。

这个工具让多个 SOLO 对话或兼容 MCP 的 agent 工具通过本地任务总线进行通信。主对话可以将任务委托给 worker 对话，等待结果，所有任务状态、进度和证据都保存在本地，带有可审计的 append-only 事件日志。

这个项目保持极简：

- 仅使用 Python 标准库（无外部依赖）
- SQLite 保存当前状态
- Append-only `data/events.jsonl` 用于审计历史
- MCP stdio server 暴露任务委托工具
- CLI 用于手动测试和检查
- 不调用外部 AI 服务的冒烟测试

## 为什么需要这个项目

在使用 SOLO 处理复杂任务时，将工作拆分为多个专门的对话会很有用：
- 主"规划"对话处理高层规划和协调
- 专门的"worker"对话处理离散、聚焦的任务（测试、文档、重构等）
- 避免让单个对话被过多上下文或过多责任压垮

这个任务总线提供了一种结构化的方式来本地协调这些对话，无需外部服务。

## 核心功能

- 从一个 agent 委托任务给另一个 agent
- 追踪任务状态：new → claimed → running → done/failed
- 附加进度更新和证据到任务
- 使用有界超时等待任务或结果
- 维护所有任务活动的可审计记录

## 工具列表

MCP server 暴露以下工具：

- `register_agent(agent_name, role?)`：注册或刷新本地 agent 身份
- `send_task(to, body, acceptance_criteria?, priority?, timeout_s?, from_agent?)`：为另一个 agent 创建任务
- `wait_for_task(agent_name, max_wait_s?, lease_s?)`：阻塞式有界等待下一个任务，然后认领
- `poll_for_task(agent_name, lease_s?)`：非阻塞检查下一个任务；如果可用则认领（找到任务返回 status="ok"，否则 status="empty"）
- `claim_task(task_id, agent_name, lease_s?)`：认领特定的 new 或 expired 任务
- `append_progress(task_id, agent_name, message, evidence?)`：附加进度和证据；将状态从 claimed → running
- `finish_task(task_id, agent_name, status, summary, changed_files?, evidence?, error_message?)`：将任务标记为 done/failed/blocked/rejected/cancelled
- `wait_for_result(task_id, max_wait_s?)`：阻塞式有界等待任务到达终端状态（超时返回 status="timeout"）
- `poll_for_result(task_id)`：非阻塞检查任务结果；终端状态返回 status="ok"，否则 status="pending"
- `get_task(task_id)`：获取完整任务，包括进度
- `list_tasks(filter?)`：列出任务，支持可选的 to/status/limit 过滤
- `codex_bus_sync(agent_name, role?, send?, claim?, finish?, watch?, list?, compact=true)`：Codex 紧凑模式组合调用，自动 register，并合并常见 bus 流程

## Codex compact mode

为了减少 Codex 在 MCP host 中的冗余 MCP round trips、降低上下文开销，并提供更紧凑的 workflow，新增了 `codex_bus_sync`。

设计原则：
- 保留全部原子工具，SOLO / TRAE 继续按现有方式使用
- `codex_bus_sync` 仅作为 Codex 的紧凑入口，不替代原子工具
- 调用时会自动执行 `register_agent(agent_name, role?)`
- `compact=true` 时返回压缩结果，省略完整 `progress` 和大段 `evidence`

推荐用法：
- planner 一次调用里同时 `register + send`
- worker 一次调用里同时 `register + claim`
- worker 一次调用里同时 `register + finish + watch`
- Codex 读取结果时优先 `watch` / `list`，避免自己写轮询循环

参数摘要：
- `agent_name`：必填，本地 agent 身份
- `role`：可选，注册时写入角色
- `send[]`：可选，元素结构与 `send_task` 对应
- `claim`：可选，支持 `enabled` / `lease_s` / `limit`
- `finish[]`：可选，元素结构与 `finish_task` 对应；默认使用当前 `agent_name`
- `watch[]`：可选，要检查结果的 `task_id` 数组
- `list`：可选，对应 `list_tasks(filter)`
- `compact`：默认 `true`

返回特点（`compact=true`）：
- 任务仅返回短字段：`task_id` / `status` / `summary` / `body`
- `watch` 返回 `status`（`pending` 或 `ok`）以及压缩版 task
- `list` 返回压缩版 task 数组

最小 Codex MCP 配置示例：

```json
{
  "mcpServers": {
    "agent-bus-codex": {
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

说明：
- 该 server 仍会暴露全部工具；对 Codex 的推荐使用方式是优先调用 `codex_bus_sync`
- 如果 host 支持工具白名单，也可以只向 Codex 暴露 `codex_bus_sync`，以进一步减少无关工具选择

最小请求示例：

```json
{
  "agent_name": "planner-main",
  "send": [
    {
      "to": "worker-docs",
      "body": "写一份 README 草稿"
    }
  ],
  "compact": true
}
```

## 阻塞 vs 轮询模式

对于 SOLO 多对话使用，某些 MCP host 可能会对同一个 stdio server 的工具调用进行串行化，这会导致长时间的 `wait_for_task` / `wait_for_result` 调用阻塞其他对话。

推荐方法：
1. **多个独立 MCP server alias**：为每个活跃的 SOLO 对话或角色配置不同的 MCP server alias，都指向同一个可执行文件，并共享同一个 `MCP_AGENT_BUS_DATA_DIR`。
2. **轮询模式**：使用 `poll_for_task` 和 `poll_for_result` 而非阻塞变体，它们立即返回而不等待。

阻塞的 `wait_for_task` / `wait_for_result` 仍然可用，推荐用于支持稳定阻塞工具调用的环境。

## 任务状态

`new`, `claimed`, `running`, `blocked`, `done`, `failed`, `rejected`, `cancelled`, `expired`

## SOLO 多对话推荐配置

建议为每个活跃的 SOLO 对话或角色配置一个独立的 MCP server alias。所有 alias 都应指向同一个 `mcp_agent_bus.server` 可执行文件，并共享同一个 `MCP_AGENT_BUS_DATA_DIR`。

**重要区别**：
- **MCP server alias**：`mcpServers` 配置对象中的键（例如 `agent-bus-planner`、`agent-bus-worker`）。这决定了 SOLO 如何识别并与该 server 通信。
- **agent_name**：通过 `register_agent()` 注册的标识符，决定任务在总线内的路由（例如 `"planner-main"`、`"worker-docs"`、`"worker-tests"`）。

共享同一个 `MCP_AGENT_BUS_DATA_DIR` 的不同 MCP server alias 可以实时相互通信，这避免了某些 MCP host 对同一个 stdio server alias 的工具调用串行化的问题。

### 推荐映射

| SOLO 对话 | MCP server alias | agent_name |
|-----------|------------------|------------|
| 主控/规划对话 | `agent-bus-planner` | `planner-main` |
| 文档 worker | `agent-bus-worker-docs` | `worker-docs` |
| 测试 worker | `agent-bus-worker-tests` | `worker-tests` |
| 审查 worker | `agent-bus-worker-review` | `worker-review` |

**重要**：调用 `send_task()` 时，`to` 参数应使用 `agent_name`，而不是 MCP server alias。例如：`send_task(to="worker-docs", ...)` 发送给 worker-docs agent，而不是 agent-bus-worker-docs server。

### 多对话配置示例

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
    "agent-bus-worker-docs": {
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
    }
  }
}
```

在此配置中：
- `agent-bus-planner` 由主"规划" SOLO 对话使用，注册为 `planner-main`
- `agent-bus-worker-docs` 由文档 worker SOLO 对话使用，注册为 `worker-docs`
- `agent-bus-worker-tests` 由测试 worker SOLO 对话使用，注册为 `worker-tests`

所有对话共享同一个 SQLite 数据库和事件日志，用于实时任务协调。

## 已验证结果

### 双 MCP Server Alias 实时通信

- **任务 ID**：task_869c4b7c23ee4818
- **配置**：
  - planner 对话使用 `agent-bus-planner` MCP server alias
  - worker 对话使用 `agent-bus-worker` MCP server alias
  - 两者共享同一个 `MCP_AGENT_BUS_DATA_DIR`
- **步骤**：
  1. planner-realtime 发送任务
  2. worker-realtime 在 `wait_for_task` 等待中收到任务
  3. worker-realtime 调用 `append_progress`
  4. worker-realtime 调用 `finish_task(done)`
  5. planner-realtime 调用 `wait_for_result` 成功读取 done 结果
- **结果**：✅ 所有步骤通过，无文件修改
- **环境**：SOLO

## 安装

### 选项 1：GitHub Clone

```bash
git clone https://github.com/SamZebrado/mcp-agent-bus.git
cd mcp-agent-bus
python3 --version # 需要 Python 3.10+
bash run_smoke.sh # 验证安装可用
```

### 选项 2：下载 ZIP

```bash
# 下载 GitHub ZIP 并解压
cd mcp-agent-bus-main
python3 --version # 需要 Python 3.10+
bash run_smoke.sh # 验证安装可用
```

然后在你的 SOLO 或 TRAE MCP 设置中添加 MCP server 配置。

### 兼容性说明

- **SOLO**：完整测试并验证
- **TRAE CN / 其他 TRAE**：如果支持 MCP stdio server 则理论上兼容，但未完全验证
- **其他 MCP host / agent**：未测试，符合 MCP stdio server 规范应该可以工作

## 双对话工作流示例

### Planner 对话
1. 调用 `register_agent("planner-main")`
2. 调用 `send_task(to="worker-docs", body="编写 README 草稿", from_agent="planner-main")` 并记下 `task_id`
3. 稍后调用 `wait_for_result(task_id, max_wait_s=300)` 检查结果

### Worker（文档）对话
1. 调用 `register_agent("worker-docs")`
2. 调用 `wait_for_task("worker-docs", max_wait_s=60)` 或使用 `poll_for_task` 获取下一个任务
3. 执行工作
4. 可选调用 `append_progress(task_id, "worker-docs", "进行中...")`
5. 调用 `finish_task(task_id, "worker-docs", "done", "README 草稿已完成", evidence={"file": "README.md"})`

## 租约行为

认领任务时会获得一个有时间限制的租约。如果任务在租约过期前未完成，任务将被标记为 `expired`，并可以被同一 worker 再次认领。这提供了超时的可见性，同时仍然允许重试。

## 项目范围与限制

关于项目能做什么和不能做什么的详细信息，请参见 [SCOPE.md](./SCOPE.md)。

关于未来计划和已知限制，请参见 [TODO.md](./TODO.md)。
