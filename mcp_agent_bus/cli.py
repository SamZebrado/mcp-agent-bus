from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .bus import AgentBus


def parse_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-agent-bus")
    parser.add_argument("--data-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register-agent")
    p.add_argument("agent_name")
    p.add_argument("--role")

    p = sub.add_parser("send-task")
    p.add_argument("to")
    p.add_argument("body")
    p.add_argument("--from-agent")
    p.add_argument("--acceptance-criteria")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--timeout-s", type=int)

    p = sub.add_parser("wait-for-task")
    p.add_argument("agent_name")
    p.add_argument("--max-wait-s", type=int, default=30)
    p.add_argument("--lease-s", type=int, default=300)

    p = sub.add_parser("poll-for-task")
    p.add_argument("agent_name")
    p.add_argument("--lease-s", type=int, default=300)

    p = sub.add_parser("claim-task")
    p.add_argument("task_id")
    p.add_argument("agent_name")
    p.add_argument("--lease-s", type=int, default=300)

    p = sub.add_parser("append-progress")
    p.add_argument("task_id")
    p.add_argument("agent_name")
    p.add_argument("message")
    p.add_argument("--evidence")

    p = sub.add_parser("finish-task")
    p.add_argument("task_id")
    p.add_argument("agent_name")
    p.add_argument("status")
    p.add_argument("summary")
    p.add_argument("--changed-files")
    p.add_argument("--evidence")
    p.add_argument("--error-message")

    p = sub.add_parser("wait-for-result")
    p.add_argument("task_id")
    p.add_argument("--max-wait-s", type=int, default=30)

    p = sub.add_parser("poll-for-result")
    p.add_argument("task_id")

    p = sub.add_parser("get-task")
    p.add_argument("task_id")

    p = sub.add_parser("list-tasks")
    p.add_argument("--filter")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bus = AgentBus(Path(args.data_dir).resolve() if args.data_dir else None)
    try:
        if args.command == "register-agent":
            result = bus.register_agent(args.agent_name, args.role)
        elif args.command == "send-task":
            result = bus.send_task(
                args.to,
                args.body,
                acceptance_criteria=parse_json(args.acceptance_criteria),
                priority=args.priority,
                timeout_s=args.timeout_s,
                from_agent=args.from_agent,
            )
        elif args.command == "wait-for-task":
            result = bus.wait_for_task(args.agent_name, args.max_wait_s, args.lease_s)
        elif args.command == "poll-for-task":
            result = bus.poll_for_task(args.agent_name, args.lease_s)
        elif args.command == "claim-task":
            result = bus.claim_task(args.task_id, args.agent_name, args.lease_s)
        elif args.command == "append-progress":
            result = bus.append_progress(args.task_id, args.agent_name, args.message, parse_json(args.evidence))
        elif args.command == "finish-task":
            result = bus.finish_task(
                args.task_id,
                args.agent_name,
                args.status,
                args.summary,
                changed_files=parse_json(args.changed_files),
                evidence=parse_json(args.evidence),
                error_message=args.error_message,
            )
        elif args.command == "wait-for-result":
            result = bus.wait_for_result(args.task_id, args.max_wait_s)
        elif args.command == "poll-for-result":
            result = bus.poll_for_result(args.task_id)
        elif args.command == "get-task":
            result = bus.get_task(args.task_id)
        elif args.command == "list-tasks":
            result = bus.list_tasks(parse_json(args.filter))
        else:
            raise AssertionError(args.command)
        print_json(result)
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    raise SystemExit(main())
