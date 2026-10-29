"""Independent opt-in light entry point. Never initializes the task bus."""
from __future__ import annotations

import argparse
import json


def capabilities() -> dict:
    return {
        "name": "mcp-agent-bus-light", "candidate": True, "default_backend": "responses",
        "responses": {"implemented": True, "transport": "stdio-mcp", "text_only": True,
                      "live_default": "disabled", "requires_explicit_approval": True},
        "harness_acp": {"implemented": False, "enabled": False,
                        "validation": "in-memory contract tests only; not real Harness or wire compatibility"},
        "task_queue": {"used": False}, "audit": {"enabled": False, "implemented": False},
        "filesystem_tools": False, "shell_tools": False, "credential_discovery": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-agent-bus-light", description=__doc__)
    parser.add_argument("--backend", choices=("responses", "harness-acp"), default="responses")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities", help="Print static capabilities; no imports of providers, files, or keys")
    sub.add_parser("serve", help="Start the independent text-only MCP server; no task queue or audit")
    sub.add_parser("chat", add_help=False, help="Independent terminal API chat; use chat --help")
    sub.add_parser("configure", add_help=False, help="Print additive MCP config only; use configure --help")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, rest = parser.parse_known_args(argv)
    if args.command == "capabilities":
        if rest:
            parser.error("Unexpected arguments for capabilities")
        print(json.dumps(capabilities(), ensure_ascii=True, sort_keys=True))
        return 0
    if args.backend == "harness-acp":
        # Lazy import only on an explicit selection; the adapter cannot launch anything.
        from .backends.harness_acp import HarnessUnavailable, require_harness_backend
        try:
            require_harness_backend()
        except HarnessUnavailable as exc:
            parser.error(str(exc))
    if args.command == "serve":
        if rest:
            parser.error("Unexpected arguments for serve")
        from .mcp_server import main as serve
        serve()
    elif args.command == "chat":
        from .chat import main as chat
        chat(rest)
    elif args.command == "configure":
        from .configure import main as configure
        configure(rest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
