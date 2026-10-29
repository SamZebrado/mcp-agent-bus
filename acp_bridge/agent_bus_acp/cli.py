"""Standalone MCP-to-ACP adapter. No process starts until explicit opt-in and per-call approval."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mcp-agent-bus-acp", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    server = sub.add_parser("serve")
    server.add_argument("--profile-file", type=Path)
    server.add_argument("--state-dir", type=Path)
    server.add_argument("--allow-agent-process", action="store_true", help="Operator opt-in: child agent can independently access files/network and incur costs; not sandboxed here")
    args = parser.parse_args(argv)
    if args.command == "capabilities":
        print(json.dumps({"name":"agent-bus-acp", "acp_protocol":1, "default_process_enabled":False,
            "sdk_dependency":"agent-client-protocol==0.12.1", "agent_loop":False, "bus_dependency":False,
            "permissions":"deny client callbacks", "os_sandbox":False, "provider_cost_limit":False,
            "real_agent_compatibility":"not yet verified; local fake ACP validation only"}))
        return 0
    from .config import BridgeError, Profile
    from .runner import Runner
    from .server import serve
    if args.allow_agent_process and (args.profile_file is None or args.state_dir is None):
        parser.error("Explicit profile-file and private state-dir required before enabling a process")
    try:
        profile = Profile.load(args.profile_file) if args.profile_file is not None else None
        serve(Runner(profile, args.state_dir, args.allow_agent_process))
    except BridgeError as exc:
        parser.error(str(exc))
    except Exception:
        parser.error("BRIDGE_STARTUP_FAILED")
    return 0
