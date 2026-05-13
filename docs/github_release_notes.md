# GitHub Release Notes

## 推荐仓库名

mcp-agent-bus

## 推荐 GitHub Description

Local SQLite-backed MCP task bus for auditable multi-agent handoffs.

## 推荐 Topics

mcp, solo, trae, agent, task-bus, multi-agent, sqlite, developer-tools

## 初版发布说明

### v0.1.0 - Initial MVP

这是 MCP Agent Task Bus 的初始 MVP 版本。

- ✅ 本地 SQLite 保存当前状态
- ✅ Append-only JSONL 事件日志用于审计
- ✅ MCP stdio server 暴露任务委托工具
- ✅ CLI 用于手动测试和检查
- ✅ 阻塞式 wait_for_task / wait_for_result
- ✅ 非阻塞 poll_for_task / poll_for_result
- ✅ 单元测试和冒烟测试
- ✅ SOLO 多对话使用文档
- ✅ 验证手动 SOLO 双对话任务接力
- ✅ 验证双 MCP server alias 实时通信

### 安装

见 README.md

### 已验证内容

- SOLO 双对话最小联通测试
- SOLO 双 MCP server alias 实时通信测试
- 本地自动化测试（16 个单元测试 + 冒烟）

### 未验证内容

- 其他 MCP host 的完整兼容性
- 大规模并发场景
- HTTP/Streamable HTTP 传输（未实现）
