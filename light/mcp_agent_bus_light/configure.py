"""Print an additive Codex MCP snippet; never reads/writes a Codex config or credential."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


def render_config(project_dir: str, platform: str, python_executable: str | None = None) -> str:
    windows = platform == "windows"
    path = PureWindowsPath(project_dir) if windows else PurePosixPath(project_dir)
    if not path.is_absolute():
        raise ValueError("Use an absolute project directory for the chosen operating system.")
    if any(ord(c) < 32 for c in project_dir):
        raise ValueError("Project directory contains a control character.")
    command = python_executable or ("py" if windows else "python3")
    args = (["-3"] if windows and python_executable is None else []) + ["-m", "mcp_agent_bus_light.mcp_server"]
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    return "\n".join([
        "# Add only this table; keep all existing Codex authentication/provider settings.",
        "# Set DUAL_LANE_* only in the parent shell or an approved secret mechanism; no keys here.",
        "[mcp_servers.agent_bus_light]", "command = " + quote(command),
        "args = " + quote(args), "cwd = " + quote(path.as_posix()),
        'env_vars = ["DUAL_LANE_API_KEY", "DUAL_LANE_ENABLE_API", "DUAL_LANE_SESSION_BUDGET_USD"]',
        'default_tools_approval_mode = "prompt"', "startup_timeout_sec = 10", "tool_timeout_sec = 100",
        "", "# Verify the effective approval UI on your exact CLI version before live use.",
    ]) + "\n"


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--platform", choices=("windows", "macos", "linux"),
                        default="windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux")
    parser.add_argument("--python", dest="python_executable")
    args = parser.parse_args(argv)
    try:
        print(render_config(args.project_dir, args.platform, args.python_executable), end="")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
