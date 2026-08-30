# Skill 安装指南

## 方式 A：让 SOLO 从 GitHub/ZIP 辅助安装

1. **获取项目**：
   - 下载 GitHub ZIP 或 clone：`git clone https://github.com/SamZebrado/mcp-agent-bus.git`
   - 解压 ZIP 或进入 clone 目录

2. **让 SOLO 打开项目**：
   - 让 SOLO 进入项目目录
   - 让 SOLO 阅读 README.md 和 .trae/skills/mcp-agent-bus/SKILL.md

3. **先验证和诊断**：
   - 运行 `bash run_smoke.sh`
   - 对计划共享的绝对 data dir 运行 `python3 -m mcp_agent_bus.cli --data-dir <path> doctor`
   - 缺失 store 时 doctor 不会替用户创建；先确认路径再初始化

4. **生成配置**：
   - 让 SOLO 根据你的需求生成 MCP server alias 配置

5. **添加 MCP server**：
   - 在 SOLO 或 TRAE 的 MCP 设置中添加配置
   - 重启或刷新 MCP

6. **运行测试**：
   - 让 SOLO 协助进行最小联通测试
   - 需要多 worker 时可运行 `scripts/smoke_three_agents.py`

## 方式 B：手动安装 Skill 文件

1. **获取项目**：同上

2. **复制 Skill 文件**：
   - 找到项目中的 `.trae/skills/mcp-agent-bus` 目录
   - 根据你使用的 TRAE/SOLO 版本，将该目录复制到对应的 Skills 目录
   - 或者使用 Skill 导入功能

3. **注意**：
   - 不同版本的 TRAE/SOLO Skills 目录可能不同，请以客户端实际导入功能或设置页面为准
   - 当前主要验证了 MCP 工具本身和 SOLO 多对话通信，不声称已在 Skill 商店发布

4. **重启/刷新**：重启或刷新 TRAE/SOLO

5. **使用**：让 SOLO 按安装检查 → doctor → alias → agent_name → 联通测试 → 多 worker 的顺序配置
