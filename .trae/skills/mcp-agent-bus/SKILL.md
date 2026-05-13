---
name: mcp-agent-bus
description: Configure and use a local MCP task bus for auditable SOLO multi-dialogue handoffs.
---

# MCP Agent Bus Skill

这个 Skill 帮助用户配置和使用 MCP Agent Task Bus，让多个 SOLO 对话可以进行任务接力、状态同步和可审计的记录。

## 作用

- 当用户想让多个 SOLO 对话协作时
- 当用户想让主对话管理 worker 对话时
- 当用户需要记录任务状态和证据时
- 当用户遇到同名 MCP server 串行调用问题时

## 操作步骤

1. **检查项目位置**：确认项目在当前工作区或用户指定目录。
2. **运行冒烟测试**：执行 `bash run_smoke.sh`，确保项目正常工作。
3. **生成 MCP server alias 配置**：根据用户需求，生成单或多 alias 配置 JSON。
4. **建议 agent_name**：为 planner 和不同 worker 建议合适的 agent_name。
5. **指导最小联通测试**：给出 planner 发任务、worker 接任务并完成、planner 读结果的步骤。
6. **选择模式**：解释阻塞 vs 轮询，推荐多 alias 方案避免串行问题。

## 输出要求

- 给出 MCP 配置 JSON
- 给出 Planner Prompt 示例
- 给出 Worker Prompt 示例
- 不要声称未验证功能
- 不自动修改用户系统配置，除非用户明确要求

## 配置示例（多 alias）

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
    }
  }
}
```

## Planner Prompt 示例

> 你是一个规划对话。使用 agent-bus-planner MCP server。
> 注册 agent 为 planner-main，给 worker-docs 发送任务，等待或轮询结果。

## Worker Prompt 示例

> 你是文档 worker 对话。使用 agent-bus-worker-docs MCP server。
> 注册 agent 为 worker-docs，等待或轮询任务，执行后提交结果和证据。
