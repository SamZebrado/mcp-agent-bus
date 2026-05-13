# 〖Skill 创作〗MCP Agent Task Bus：让 SOLO 多对话进行可审计任务接力

## Skill 简介

MCP Agent Task Bus 是一个本地 MCP 任务总线工具，它让多个 SOLO 对话或兼容 MCP 的 agent 工具通过本地存储进行任务分发、状态同步、结果回传和审计记录。

## 解决的问题

在处理复杂开发任务时，单个 SOLO 对话可能会因为上下文过多或责任太分散而效率降低。本项目提供了一种结构化的方式：
- 主对话（"planner"）负责任务规划和协调
- 专门的子对话（"worker"）负责执行具体的、聚焦的任务（测试、文档整理、重构等）
- 所有任务状态变更和证据都保留在本地，便于审计和回溯

## 创作过程

- 首先实现核心 bus.py：提供 SQLite + append-only 事件日志的任务管理
- 然后实现 MCP server.py：暴露工具接口
- 添加 CLI：用于手动测试
- 增强非阻塞 poll_for_task / poll_for_result：解决某些 host 串行调用问题
- 添加多对话配置说明
- 验证真实 SOLO 双对话实时通信

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
- Append-only 事件日志，支持审计
- 任务租约过期机制，避免任务卡死
- 阻塞式和非阻塞式工具调用选项

## 使用步骤

1. **准备项目**：
   - 确保项目位于本地，Python 3.10+ 可用
   - 运行 `bash run_smoke.sh` 验证项目可用

2. **配置 MCP server**：
   - 在每个需要协作的 SOLO 对话的 MCP 设置中添加对应 alias
   - 推荐多 alias 共享同一 data dir

3. **主对话流程**：
   - 调用 register_agent 注册自己
   - 调用 send_task 发送任务
   - 稍后调用 wait_for_result 或 poll_for_result 获取结果

4. **子对话流程**：
   - 调用 register_agent 注册自己
   - 调用 wait_for_task 或 poll_for_task 等待任务
   - 执行任务
   - 可选调用 append_progress
   - 调用 finish_task 提交结果

## 效果展示

### 已验证演示：双 MCP Server Alias 实时通信

- **MCP Server Alias**：
  - Planner：`agent-bus-planner`
  - Worker：`agent-bus-worker`
- **Task ID**：task_869c4b7c23ee4818
- **流程**：
  1. Planner 发送任务
  2. Worker 在 wait_for_task 等待中成功收到任务
  3. Worker 调用 append_progress
  4. Worker 调用 finish_task(done)
  5. Planner 调用 wait_for_result 成功读取 done 结果
- **结果**：✅ 所有步骤通过，无文件修改
- **环境**：SOLO

## Skill 链接 / GitHub

- **GitHub 仓库**：https://github.com/SamZebrado/mcp-agent-bus

## 总结与思考

- 本项目保持极简，仅使用 Python 标准库
- 多 alias 共享同一 data dir 方案有效解决了某些 host 串行调用问题
- 不声称万能控制器、控制任意 AI、免费 agent pool、保证完成所有任务等未验证功能
- 未来可以添加更多功能（如心跳、父子任务、显式接受拒绝、HTTP 传输等）
