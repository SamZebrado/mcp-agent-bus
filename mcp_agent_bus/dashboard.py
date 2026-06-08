from __future__ import annotations

import argparse
import html
import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .dashboard_data import DashboardStore


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def fmt_ts(value: Any) -> str:
    if value is None:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))
    except (TypeError, ValueError):
        return esc(value)


def fmt_age(value: Any, now: float | None = None) -> str:
    if value is None:
        return ""
    now = now or time.time()
    try:
        seconds = max(0, int(now - float(value)))
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def json_pretty(value: Any) -> str:
    return esc(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


class DashboardHandler(BaseHTTPRequestHandler):
    store: DashboardStore

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.render_overview(parsed.query)
            return
        if parsed.path.startswith("/task/"):
            self.render_task(unquote(parsed.path.removeprefix("/task/")))
            return
        if parsed.path == "/healthz":
            self.send_html("ok", title="ok")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def render_overview(self, query: str) -> None:
        params = parse_qs(query)
        status = one(params.get("status"))
        to = one(params.get("to"))
        q = one(params.get("q"))
        data = self.store.overview(status=status, to=to, q=q)

        body = [
            "<section>",
            "<div class='toolbar'>",
            "<a class='button' href='/'>Clear</a>",
            "<button onclick='location.reload()'>Refresh</button>",
            f"<span class='muted'>Data dir: <code>{esc(data['data_dir'])}</code></span>",
            "</div>",
            "<form class='filters' method='get'>",
            select("status", data["statuses"], status, "All statuses"),
            select("to", data["assignees"], to, "All assignees"),
            f"<input name='q' value='{esc(q)}' placeholder='Search task, body, summary, agent'>",
            "<button type='submit'>Apply</button>",
            "</form>",
            "</section>",
        ]
        if not data["db_exists"]:
            body.append(
                f"<p class='notice'>No SQLite database found at <code>{esc(data['db_path'])}</code>. "
                "Start an MCP bus once or point --data-dir at an existing task-board directory.</p>"
            )

        body.extend(["<h2>Agents</h2>", "<table>", agent_header()])
        for agent in data["agents"]:
            body.append(agent_row(agent))
        body.append("</table>")

        body.extend(["<h2>Tasks</h2>", "<table>", task_header()])
        for task in data["tasks"]:
            body.append(task_row(task, data["now"]))
        body.append("</table>")

        body.extend(["<h2>Recent Events</h2>", event_list(data["events"])])
        self.send_html("\n".join(body), title="mcp-agent-bus dashboard")

    def render_task(self, task_id: str) -> None:
        data = self.store.task_detail(task_id)
        if data is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Task not found")
            return
        task = data["task"]
        body = [
            "<div class='toolbar'>",
            "<a class='button' href='/'>Dashboard</a>",
            "<button onclick='location.reload()'>Refresh</button>",
            f"<button onclick=\"navigator.clipboard.writeText('{esc(task_id)}')\">Copy task_id</button>",
            "</div>",
            f"<h2>{esc(task_id)}</h2>",
            "<section class='grid'>",
            fact("Status", badge(task.get("status"))),
            fact("Sender", esc(task.get("from_agent"))),
            fact("Assignee", esc(task.get("to_agent"))),
            fact("Priority", esc(task.get("priority"))),
            fact("Created", fmt_ts(task.get("created_at"))),
            fact("Updated", fmt_ts(task.get("updated_at"))),
            fact("Age", fmt_age(task.get("created_at"), data["now"])),
            "</section>",
            "<h3>Task Description</h3>",
            f"<pre>{esc(task.get('body'))}</pre>",
            "<h3>Acceptance Criteria</h3>",
            f"<pre>{json_pretty(task.get('acceptance_criteria'))}</pre>",
            "<h3>Progress Updates</h3>",
            progress_list(task.get("progress", [])),
            "<h3>Result Summary</h3>",
            f"<pre>{esc(task.get('summary'))}</pre>",
            "<h3>Changed Files</h3>",
            f"<pre>{json_pretty(task.get('changed_files'))}</pre>",
            "<h3>Evidence</h3>",
            f"<pre>{json_pretty(task.get('evidence'))}</pre>",
            "<h3>Event Timeline</h3>",
            event_list(data["events"]),
            "<details><summary>Raw JSON</summary>",
            f"<pre>{json_pretty(data['raw'])}</pre>",
            "</details>",
        ]
        self.send_html("\n".join(body), title=f"task {task_id}")

    def send_html(self, body: str, title: str) -> None:
        page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <title>{esc(title)}</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <h1>mcp-agent-bus Dashboard</h1>
    <p>Read-only localhost observer for local task-card metadata.</p>
  </header>
  <main>{body}</main>
</body>
</html>"""
        encoded = page.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def one(values: list[str] | None) -> str | None:
    if not values:
        return None
    value = values[0].strip()
    return value or None


def select(name: str, values: list[str], selected: str | None, empty_label: str) -> str:
    items = [f"<option value=''>{esc(empty_label)}</option>"]
    for value in values:
        attr = " selected" if selected == value else ""
        items.append(f"<option value='{esc(value)}'{attr}>{esc(value)}</option>")
    return f"<select name='{esc(name)}'>{''.join(items)}</select>"


def agent_header() -> str:
    return (
        "<tr><th>Agent</th><th>Role</th><th>Last seen</th><th>State</th>"
        "<th>Claimed</th><th>Running</th><th>Finished</th><th>Failed</th></tr>"
    )


def agent_row(agent: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td><code>{esc(agent.get('agent_name'))}</code></td>"
        f"<td>{esc(agent.get('role'))}</td>"
        f"<td>{fmt_ts(agent.get('last_seen_at'))}</td>"
        f"<td>{badge(agent.get('inferred_status'))}</td>"
        f"<td>{esc(agent.get('claimed_count'))}</td>"
        f"<td>{esc(agent.get('running_count'))}</td>"
        f"<td>{esc(agent.get('finished_count'))}</td>"
        f"<td>{esc(agent.get('failed_count'))}</td>"
        "</tr>"
    )


def task_header() -> str:
    return (
        "<tr><th>Task</th><th>Title / Summary</th><th>From</th><th>To</th><th>Status</th>"
        "<th>Priority</th><th>Created</th><th>Updated</th><th>Age</th><th></th></tr>"
    )


def task_row(task: dict[str, Any], now: float) -> str:
    task_id = str(task.get("task_id"))
    return (
        "<tr>"
        f"<td><code>{esc(task_id)}</code></td>"
        f"<td>{esc(task.get('title'))}</td>"
        f"<td>{esc(task.get('from_agent'))}</td>"
        f"<td>{esc(task.get('to_agent'))}</td>"
        f"<td>{badge(task.get('status'))}</td>"
        f"<td>{esc(task.get('priority'))}</td>"
        f"<td>{fmt_ts(task.get('created_at'))}</td>"
        f"<td>{fmt_ts(task.get('updated_at'))}</td>"
        f"<td>{fmt_age(task.get('created_at'), now)}</td>"
        f"<td><a href='/task/{quote(task_id)}'>detail</a></td>"
        "</tr>"
    )


def badge(value: Any) -> str:
    text = esc(value)
    css = f"badge badge-{text}" if text else "badge"
    return f"<span class='{css}'>{text}</span>"


def fact(label: str, value: str) -> str:
    return f"<div class='fact'><span>{esc(label)}</span><strong>{value}</strong></div>"


def progress_list(progress: list[dict[str, Any]]) -> str:
    if not progress:
        return "<p class='muted'>No progress updates.</p>"
    items = []
    for item in progress:
        evidence = item.get("evidence")
        items.append(
            "<li>"
            f"<time>{fmt_ts(item.get('created_at'))}</time> "
            f"<strong>{esc(item.get('agent_name'))}</strong>: {esc(item.get('message'))}"
            + (f"<pre>{json_pretty(evidence)}</pre>" if evidence is not None else "")
            + "</li>"
        )
    return f"<ol class='timeline'>{''.join(items)}</ol>"


def event_list(events: list[dict[str, Any]]) -> str:
    if not events:
        return "<p class='muted'>No events.</p>"
    items = []
    for event in events:
        label = event.get("event_type")
        items.append(
            "<li>"
            f"<time>{fmt_ts(event.get('ts'))}</time> "
            f"{badge(label)} "
            f"<code>{esc(event.get('task_id'))}</code> "
            f"<span>{esc(event.get('agent_name'))}</span>"
            f"<details><summary>payload</summary><pre>{json_pretty(event.get('payload', event))}</pre></details>"
            "</li>"
        )
    return f"<ol class='timeline'>{''.join(items)}</ol>"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-agent-bus-dashboard")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("MCP_AGENT_BUS_DATA_DIR", "data")))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = DashboardStore(args.data_dir)

    class Handler(DashboardHandler):
        pass

    Handler.store = store
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mcp-agent-bus dashboard listening on http://{args.host}:{args.port}")
    print(f"data dir: {store.data_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


CSS = """
:root { color-scheme: light; --border:#d8dee4; --muted:#57606a; --bg:#f6f8fa; --fg:#24292f; }
body { margin:0; font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--fg); background:white; }
header { padding:20px 28px; background:var(--bg); border-bottom:1px solid var(--border); }
h1 { margin:0; font-size:24px; }
h2 { margin-top:28px; }
h3 { margin-top:22px; }
main { padding:20px 28px 48px; }
table { width:100%; border-collapse:collapse; margin:12px 0 24px; }
th, td { border-bottom:1px solid var(--border); padding:8px 10px; text-align:left; vertical-align:top; }
th { background:var(--bg); font-weight:600; }
code, pre { font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
pre { white-space:pre-wrap; overflow:auto; padding:12px; background:var(--bg); border:1px solid var(--border); border-radius:6px; }
a { color:#0969da; text-decoration:none; }
a:hover { text-decoration:underline; }
button, .button, input, select { border:1px solid var(--border); border-radius:6px; background:white; color:var(--fg); padding:6px 10px; font:inherit; }
button, .button { cursor:pointer; }
.toolbar, .filters { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
.filters input { min-width:280px; }
.muted { color:var(--muted); }
.notice { padding:12px; border:1px solid #bf8700; background:#fff8c5; border-radius:6px; }
.badge { display:inline-block; border:1px solid var(--border); border-radius:999px; padding:1px 8px; background:#fff; font-size:12px; }
.badge-done, .badge-active { background:#dafbe1; border-color:#8ddb8c; }
.badge-failed, .badge-rejected { background:#ffebe9; border-color:#ff8182; }
.badge-running, .badge-claimed { background:#ddf4ff; border-color:#54aeff; }
.badge-stale, .badge-expired { background:#fff8c5; border-color:#d4a72c; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
.fact { border:1px solid var(--border); border-radius:6px; padding:10px; }
.fact span { display:block; color:var(--muted); font-size:12px; }
.timeline { padding-left:24px; }
.timeline li { margin:10px 0; }
time { color:var(--muted); margin-right:8px; }
details { margin-top:6px; }
summary { cursor:pointer; color:var(--muted); }
"""


if __name__ == "__main__":
    raise SystemExit(main())
