# SOLO 双对话测试计划与记录

## 已验证的测试

### 1. 最小联通测试（手动 SOLO 测试）

- **测试时间**: 2026-05-13
- **任务 ID**: task_5e01987ce0e14439
- **流程**:
  1. planner-minimal 发送任务
  2. worker-minimal 成功 wait_for_task 接收任务
  3. worker-minimal 调用 append_progress
  4. worker-minimal 调用 finish_task 标记为 done
  5. planner-minimal 调用 get_task 成功读取到 done 状态的结果
- **结果**: ✅ 通过
- **文件修改**: 无

### 2. 双 MCP Server Alias 实时通信测试（手动 SOLO 测试）

- **测试时间**: 2026-05-13
- **配置**:
  - planner 对话使用 `agent-bus-planner` MCP server alias
  - worker 对话使用 `agent-bus-worker` MCP server alias
  - 两者共享同一个 `MCP_AGENT_BUS_DATA_DIR`
- **任务 ID**: task_869c4b7c23ee4818
- **流程**:
  1. planner-realtime 调用 send_task 发送任务
  2. worker-realtime 在 wait_for_task 等待状态下成功收到任务
  3. worker-realtime 调用 append_progress
  4. worker-realtime 调用 finish_task(done)
  5. planner-realtime 调用 wait_for_result 成功读取 done 结果
- **结果**: ✅ 通过
- **文件修改**: 无

### 3. 本地自动化测试

```bash
bash run_smoke.sh
```

- **结果**: ✅ SMOKE OK
- **测试数量**: 16 个 unittest + 1 个 smoke 流程
- **状态**: 全部通过

## 观察到的限制

### 同名 MCP Server Alias 串行化问题

某些 MCP host 可能会对同一个 stdio server 的 tool calls 进行串行化处理。当 worker 对话在同一个 agent-bus server 上长时间调用 wait_for_task 时，planner 对话的 register_agent 等其他调用可能会被阻塞。重启 MCP 后恢复正常。

### 推荐解决方案

1. **多个 MCP server alias 共享同一个 data dir**:
   - 为 planner 和 worker 分别配置不同的 MCP server alias
   - 两者共享同一个 `MCP_AGENT_BUS_DATA_DIR`
   - 这样可以避免串行化问题，实现实时通信

2. **使用 poll_for_task / poll_for_result 作为非阻塞替代**:
   - 适用于不想配置多个 MCP server alias 的场景
   - 返回立即结果，不会在 MCP host 端造成阻塞

3. **后续可考虑的方案**（当前未实现）:
   - HTTP / Streamable HTTP transport
   - 注意：当前不要声称已经支持这些传输方式

## 推荐测试方式

### 方式一：多个 MCP Server Alias（推荐）

1. planner: send_task
2. worker: wait_for_task 或 poll_for_task
3. worker: append_progress
4. worker: finish_task(done)
5. planner: get_task / wait_for_result / poll_for_result

### 方式二：单个 MCP Server Alias + Polling

1. planner: send_task
2. worker: poll_for_task（非阻塞）
3. worker: append_progress
4. worker: finish_task(done)
5. planner: poll_for_result（非阻塞）

## 验证总结

| 测试类型 | 自动化 | 验证结果 |
|---------|--------|---------|
| bash run_smoke.sh | ✅ 自动 | ✅ 通过 |
| 最小联通测试 | ❌ 手动 SOLO | ✅ 通过 |
| 双 MCP alias 实时通信 | ❌ 手动 SOLO | ✅ 通过 |
