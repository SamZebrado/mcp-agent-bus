from __future__ import annotations

import json
import multiprocessing
import shutil
import tempfile
from pathlib import Path

from mcp_agent_bus.bus import AgentBus


def worker(data_dir: str, agent_name: str, expected_body: str, summary: str) -> None:
    bus = AgentBus(Path(data_dir))
    try:
        bus.register_agent(agent_name, "worker")
        claimed = bus.poll_for_task(agent_name, lease_s=30)
        assert claimed["status"] == "ok", claimed
        task = claimed["task"]
        assert task["body"] == expected_body, task
        bus.append_progress(task["task_id"], agent_name, "Work started.", {"process": agent_name})
        bus.finish_task(
            task["task_id"],
            agent_name,
            "done",
            summary,
            evidence={"worker": agent_name, "verified": True},
        )
    finally:
        bus.close()


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="mcp-agent-bus-three-agent-"))
    planner = AgentBus(root)
    try:
        planner.register_agent("planner-main", "planner")
        tests_task = planner.send_task(
            "worker-tests",
            "Run focused tests.",
            from_agent="planner-main",
            client_request_id="demo-tests-1",
        )
        docs_task = planner.send_task(
            "worker-docs",
            "Review concise docs.",
            from_agent="planner-main",
            client_request_id="demo-docs-1",
        )

        ctx = multiprocessing.get_context("spawn")
        processes = [
            ctx.Process(
                target=worker,
                args=(str(root), "worker-tests", "Run focused tests.", "Tests passed."),
            ),
            ctx.Process(
                target=worker,
                args=(str(root), "worker-docs", "Review concise docs.", "Docs reviewed."),
            ),
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0, process.exitcode

        results = [
            planner.wait_for_result(tests_task["task_id"], max_wait_s=2)["task"],
            planner.wait_for_result(docs_task["task_id"], max_wait_s=2)["task"],
        ]
        for result in results:
            assert result["status"] == "done", result
            planner.accept_task_result(result["task_id"], "planner-main", "Demo aggregate verified.")

        reviewed = [planner.get_task(result["task_id"]) for result in results]
        assert all(task and task["result_review"] == "accepted" for task in reviewed), reviewed
        events = [
            json.loads(line)
            for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len({event["event_id"] for event in events}) == len(events), events
        report = planner.doctor()
        assert report["status"] == "PASS", report

        print("MULTI_AGENT_SMOKE OK")
        print(f"tasks={','.join(task['task_id'] for task in reviewed if task)}")
        print("workers=worker-tests,worker-docs")
        print("reviews=accepted,accepted")
        print(f"events={len(events)}")
        print(f"doctor={report['status']}")
        return 0
    finally:
        planner.close()
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
