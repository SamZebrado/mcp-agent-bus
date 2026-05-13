# MCP Agent Task Bus: 让 SOLO 多对话进行可审计任务接力

## Skill 简介

MCP Agent Task Bus 是一个本地任务总线工具，它让多个 SOLO 对话或兼容 MCP 的 agent 工具能够通过本地存储进行任务分发、状态同步、结果回传和审计记录。

## 解决的问题

在处理复杂开发任务时，单个 SOLO 对话可能会因为上下文过多或责任太分散而效率降低。本项目提供了一个结构化的方式：
- 主对话（"planner"）可以负责任务规划和协调
- 专门的子对话（"worker"）可以负责执行具体的、聚焦的任务（测试、文档整理、重构等）
- 所有任务状态变更和证据都会被保留在本地，便于审计和回溯

## 使用场景

- 复杂项目的任务拆分与协作
- 主对话负责规划，子对话负责执行具体任务
- 需要保留任务执行历史和证据的场景
- 本地多 agent 协作，不依赖外部服务

## 核心功能

- Agent 身份注册与管理
- 任务发送与认领（支持优先级和超时）
- 任务状态追踪（new → claimed → running → done/failed）
- 进度更新与证据附加
- 任务完成与结果回传
- 可配置的本地数据存储
-  append-only 事件日志，支持审计
- 任务租约过期机制，避免任务卡死

## 使用步骤

### 1. 准备

确保项目位于本地，并且 Python 3.10+ 可用。

### 2. 在 SOLO 对话中配置 MCP 服务器

在每个需要参与协作的 SOLO 对话的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "agent-bus": {
      "command": "python3",
      "args": ["-m", "mcp_agent_bus.server"],
      "env": {
        "PYTHONPATH": "/path/to/mcp-agent-bus",
        "MCP_AGENT_BUS_DATA_DIR": "/path/to/your/data/dir"
      }
    }
  }
}
```

### 3. 主对话（Planner）流程

1. 调用 `register_agent("planner")` 注册自己
2. 调用 `send_task(to="worker", body="具体任务内容", from_agent="planner")` 发送任务，记录返回的 `task_id`
3. 稍后调用 `wait_for_result(task_id, max_wait_s=...)` 获取结果

### 4. 子对话（Worker）流程

1. 调用 `register_agent("worker")` 注册自己
2. 调用 `wait_for_task("worker", max_wait_s=...)` 等待并认领任务
3. 执行任务
4. 可选：调用 `append_progress(task_id, "worker", "进度信息")` 报告进度
5. 调用 `finish_task(task_id, "worker", "done", "总结", evidence={...})` 提交结果

## 示例流程

假设有两个 SOLO 对话：

**对话 A（规划者）**
1. 注册为 planner
2. 发送任务："为新功能编写单元测试"给 worker
3. 继续处理其他事情或等待结果

**对话 B（工作者）**
1. 注册为 worker
2. 等待并认领任务
3. 编写单元测试
4. 提交结果，包含修改的文件和测试输出作为证据

**对话 A**
1. 获取任务结果
2. 审查并验收

## 效果展示占位

（此处可添加实际使用示例、截图或演示链接）

## 当前已验证内容

- 双 agent 基本任务接力流程
- 任务状态管理
- 事件日志 append-only 特性
- 数据目录可配置
- CLI 基本命令可用
- 单元测试覆盖核心功能（16 个 unittest + 1 个 smoke 流程）
- Smoke 测试通过
- poll_for_task / poll_for_result 非阻塞工具

## 已验证演示

### 双 MCP Server Alias 实时通信

- **MCP Server Alias**:
  - planner: `agent-bus-planner`
  - worker: `agent-bus-worker`
- **任务 ID**: task_869c4b7c23ee4818
- **流程**:
  - planner 发送任务
  - worker 在等待状态下成功收到 planner 发来的任务
  - worker 调用 append_progress 和 finish_task(done)
  - planner 成功读取 done 结果
- **结论**: 多个 MCP server alias 共享同一个 data dir，可以实现 SOLO 多对话实时协作

## 后续计划

- 添加任务心跳工具，允许在不追加进度的情况下延长租约
- 支持父子任务关系，用于多步骤委托树
- 添加任务结果验收流程，允许 planner 显式接受或拒绝完成的工作
- 为所有工具参数添加紧凑的 JSON Schema 验证
- 添加可选的 HTTP/SSE 传输，适用于不使用 stdio 的主机
- 添加清理/导出命令，用于归档任务和事件日志
- 如果总线暴露在受信任的本地进程边界之外，添加 per-agent 授权令牌

（注意：兼容 MCP 的 agent 工具可以进一步接入。）
