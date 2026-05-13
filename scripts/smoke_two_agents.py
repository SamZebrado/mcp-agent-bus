from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from mcp_agent_bus.bus import AgentBus


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mcp-agent-bus-smoke-"))
    try:
        bus = AgentBus(tmp)
        bus.register_agent("agent-a", "planner")
        bus.register_agent("agent-b", "worker")

        sent = bus.send_task(
            to="agent-b",
            from_agent="agent-a",
            body="Create a tiny handoff note for the planner.",
            acceptance_criteria=["summary is present", "evidence contains smoke=true"],
            priority=10,
            timeout_s=60,
        )
        task_id = sent["task_id"]

        claimed = bus.wait_for_task("agent-b", max_wait_s=2, lease_s=30)
        assert claimed["status"] == "ok", claimed
        assert claimed["task"]["task_id"] == task_id, claimed
        assert claimed["task"]["status"] == "claimed", claimed

        bus.append_progress(task_id, "agent-b", "Started local smoke work.", {"step": 1})
        bus.finish_task(
            task_id,
            "agent-b",
            "done",
            "Smoke worker completed the delegated task.",
            changed_files=["README.md"],
            evidence={"smoke": True},
        )

        result = bus.wait_for_result(task_id, max_wait_s=2)
        assert result["status"] == "ok", result
        assert result["task"]["status"] == "done", result
        assert result["task"]["summary"] == "Smoke worker completed the delegated task.", result

        timeout = bus.wait_for_task("agent-b", max_wait_s=1, lease_s=30)
        assert timeout["status"] == "timeout", timeout

        events = (tmp / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(events) >= 5, events
        event_types = [json.loads(line)["event_type"] for line in events]
        assert "task_sent" in event_types, event_types
        assert "task_claimed" in event_types, event_types
        assert "task_finished" in event_types, event_types

        print("SMOKE OK")
        print(f"task_id={task_id}")
        print(f"events={len(events)}")
        print(f"db={tmp / 'mcp_agent_bus.sqlite'}")
        return 0
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    raise SystemExit(main())
