# Run Record

## Automated Local Verification

### 2026-05-13 - Latest smoke test

Command:

```bash
bash run_smoke.sh
```

Result:

```bash
SMOKE OK
task_id=task_eadffe77df514ebd
events=6
db=/var/folders/54/m91z96515nx5hgqnj2lskzr40000gn/T/mcp-agent-bus-smoke-slr6z6qo/mcp_agent_bus.sqlite
................
----------------------------------------------------------------------
Ran 16 tests in 3.395s

OK
```

### 2026-06-08 - Codex config / Auto-review docs patch

Command:

```bash
bash run_smoke.sh
```

Result:

```bash
SMOKE OK
task_id=task_1cb36ceda8ae4b80
events=6
db=/var/folders/54/m91z96515nx5hgqnj2lskzr40000gn/T/mcp-agent-bus-smoke-r4nihfjb/mcp_agent_bus.sqlite
..............................
----------------------------------------------------------------------
Ran 30 tests in 3.460s

OK
```

Command:

```bash
PYTHONPATH="$PWD" python3 -m unittest discover -s tests
```

Result:

```bash
..............................
----------------------------------------------------------------------
Ran 30 tests in 3.403s

OK
```

Command:

```bash
python3 -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('docs/codex_mcp_config_example.toml').read_text()); print('TOML OK')"
```

Result:

```bash
TOML OK
```

### 2026-06-08 - Read-only dashboard MVP

Command:

```bash
bash run_smoke.sh
```

Result:

```bash
SMOKE OK
task_id=task_d457eaeb4e164051
events=6
db=/var/folders/54/m91z96515nx5hgqnj2lskzr40000gn/T/mcp-agent-bus-smoke-v5adg21f/mcp_agent_bus.sqlite
...................................
----------------------------------------------------------------------
Ran 35 tests in 3.682s

OK
```

Command:

```bash
PYTHONPATH="$PWD" python3 -m unittest discover -s tests
```

Result:

```bash
...................................
----------------------------------------------------------------------
Ran 35 tests in 3.507s

OK
```

Command:

```bash
python3 -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('docs/codex_mcp_config_example.toml').read_text()); print('TOML OK')"
```

Result:

```bash
TOML OK
```

Command:

```bash
PYTHONPATH="$PWD" python3 -c "import mcp_agent_bus.dashboard; print('DASHBOARD IMPORT OK')"
```

Result:

```bash
DASHBOARD IMPORT OK
```

Command:

```bash
python3 -m mcp_agent_bus.dashboard --data-dir /tmp/mcp-agent-bus-dashboard-smoke --host 127.0.0.1 --port 8765
curl -s http://127.0.0.1:8765/healthz
lsof -ti tcp:8765
kill 44474
curl -s http://127.0.0.1:8765/healthz
```

Result:

```bash
Dashboard health endpoint returned HTML containing "ok".
lsof returned PID 44474.
After kill, curl exited with code 7, confirming the smoke server stopped.
```

Note:
- `bash run_smoke.sh` 当前包含两个阶段：
  1. `scripts/smoke_two_agents.py`：1 个端到端 smoke 流程
  2. `python3 -m unittest discover -s tests`：当前运行 16 个 unittest 测试
- 总计：1 个 smoke 流程 + 16 个 unittest 测试，全部通过

## Manual SOLO Verification

### 2026-05-13 - Minimal two-dialogue test

- planner-minimal 发送任务
- worker-minimal 收到任务
- worker-minimal 调用 append_progress
- worker-minimal 调用 finish_task(done)
- planner-minimal 能 get_task 读取结果
- **任务 ID**: task_5e01987ce0e14439
- **结果**: ✅ 通过
- **文件修改**: 无

### 2026-05-13 - Dual MCP server alias real-time communication test

- planner 对话使用 `agent-bus-planner` MCP server alias
- worker 对话使用 `agent-bus-worker` MCP server alias
- 两者共享同一个 `MCP_AGENT_BUS_DATA_DIR`
- **任务 ID**: task_869c4b7c23ee4818
- worker-realtime 在 wait_for_task 等待状态下成功收到任务
- worker-realtime 调用 append_progress
- worker-realtime 调用 finish_task(done)
- planner-realtime 调用 wait_for_result 成功读取 done 结果
- **结果**: ✅ 通过
- **文件修改**: 无

## Historical Records

### 2026-05-13 - Initial smoke test (Codex baseline)

```bash
SMOKE OK
task_id=task_b92b99a206af4470
events=6
db=/var/folders/54/m91z96515nx5hgqnj2lskzr40000gn/T/mcp-agent-bus-smoke-k3u48x_7/mcp_agent_bus.sqlite
.....
----------------------------------------------------------------------
Ran 5 tests in 2.284s

OK
```

Note: The "Ran 5 tests" reflects the original test suite size at that time.
