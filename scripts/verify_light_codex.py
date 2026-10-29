"""Zero-cost additive light-package test using the real Codex CLI app-server.

No user credentials/configuration are read. This runs an ephemeral thread without
starting a model turn. It exercises CLI MCP discovery and calls, not subscription
login, Desktop UI approvals, or real OpenAI billing. The MCP process is the real
project server with only its HTTP opener replaced by a fixed in-memory fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
LIGHT_ROOT = ROOT / "light"
QUESTION = "Offline integration test: reply with BRIDGE_MOCK_OK."
FAKE_KEY = "sk-offline-fixture-not-a-real-key"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def wrapper(evidence):
    """Preserve the protocol/server while making all transport in-memory."""
    sys.path.insert(0, str(LIGHT_ROOT))
    from mcp_agent_bus_light import api, mcp_server

    def audit(event):
        with (evidence / "mcp-wire.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            return self.body[:size]

    def fake_opener(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_header("Authorization") == "Bearer " + FAKE_KEY
        assert payload["store"] is False
        assert payload["input"] == [{"role": "user", "content": QUESTION}]
        audit({"event": "mock_http", "url": request.full_url, "payload": payload,
               "authorization": "fixed test placeholder; omitted", "network_used": False})
        f = FakeResponse()
        f.body = json.dumps({
            "id": "resp_offline_fixture", "object": "response", "status": "completed",
            "model": payload["model"], "service_tier": "default",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "BRIDGE_MOCK_OK"}]}],
            "usage": {"input_tokens": 16, "output_tokens": 8,
                      "input_tokens_details": {"cached_tokens": 0}},
        }).encode()
        return f

    original_call = api.call_api

    def offline_call(*args, **kwargs):
        kwargs["opener"] = fake_opener
        return original_call(*args, **kwargs)

    mcp_server.call_api = offline_call
    original_process = mcp_server.process

    def observed_process(message):
        result = original_process(message)
        audit({"request": message, "response": result})
        return result

    mcp_server.process = observed_process
    # Hard safety guard: even if a future code path forgets the injected opener,
    # no socket can be created in this test MCP child. Normal stdio still works.
    import socket

    def no_network(*args, **kwargs):
        raise RuntimeError("Network disabled in offline MCP fixture")

    socket.socket = no_network
    socket.create_connection = no_network
    from mcp_agent_bus_light.cli import main
    main(["serve"])


class RpcClient:
    def __init__(self, command, env, cwd, evidence):
        self.evidence = evidence
        self.events = []
        self.ident = 0
        self.queue = queue.Queue()
        self.stderr_file = (evidence / "cli-stderr.txt").open("w", encoding="utf-8")
        self.p = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.stderr_file,
                                  text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.p.stdout:
            try:
                self.queue.put(json.loads(line))
            except json.JSONDecodeError:
                self.queue.put({"invalid_stdout": line})
        self.queue.put({"eof": True})

    def request(self, method, params=None, timeout=30):
        self.ident += 1
        req = {"id": self.ident, "method": method, "params": params or {}}
        self.events.append({"sent": req})
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            event = self.queue.get(timeout=max(.01, deadline - time.monotonic()))
            self.events.append({"received": event})
            if event.get("eof"):
                raise RuntimeError("Codex app-server closed unexpectedly")
            if event.get("id") == self.ident:
                if "error" in event:
                    raise RuntimeError(json.dumps(event["error"]))
                return event["result"]
            if time.monotonic() >= deadline:
                raise TimeoutError(method)

    def notify(self, method):
        self.p.stdin.write(json.dumps({"method": method}) + "\n")
        self.p.stdin.flush()

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.stderr_file.close()
        write_json(self.evidence / "app-server-events.json", self.events)


def run(codex, evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    if any(evidence.iterdir()):
        raise ValueError("Use a fresh empty evidence directory for each run; prior results are preserved.")
    # A new private HOME every run; never inspect or copy the user's Codex home.
    home = Path(tempfile.mkdtemp(prefix="bus-light-cli-home-"))
    env = {"HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(home),
           "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
           "PYTHONUTF8": "1", "LANG": "C.UTF-8", "NO_PROXY": "127.0.0.1,localhost",
           "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
           "ALL_PROXY": "http://127.0.0.1:9"}
    if os.name == "nt":
        # Windows loader variable is not a credential; required for Python/CLI.
        env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
    version = subprocess.check_output([codex, "--version"], env=env, text=True, timeout=10).strip()
    command = [codex, "mcp", "add", "agent_bus_light", "--env", "DUAL_LANE_API_KEY=" + FAKE_KEY,
               "--env", "DUAL_LANE_ENABLE_API=1",
               "--env", "DUAL_LANE_SESSION_BUDGET_USD=0.0003",
               "--", sys.executable, str(Path(__file__).resolve()), "--mcp-wrapper",
               "--evidence-dir", str(evidence.resolve())]
    registration = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True,
                                  check=True, timeout=15)
    write_json(evidence / "cli-registration.json", {
        "version": version, "exit_code": registration.returncode,
        "stdout": registration.stdout, "stderr": registration.stderr,
        "isolation": "new HOME/CODEX_HOME and allowlisted environment; fixed fake API key only"})
    # This provider is an unreachable loopback placeholder, never a real model.
    # No turn/start is issued. The override prevents any auth or model fetch.
    config = (home / "config.toml").read_text(encoding="utf-8")
    config = ('model_provider = "offline_smoke"\nmodel = "offline-smoke-no-inference"\n'
              'cli_auth_credentials_store = "ephemeral"\n'
              + config
              + '\n[model_providers.offline_smoke]\nname = "Offline smoke; no inference"\n'
              'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\n'
              'requires_openai_auth = false\nrequest_max_retries = 0\n'
              '\n[analytics]\nenabled = false\n')
    (home / "config.toml").write_text(config, encoding="utf-8")
    client = RpcClient([codex, "app-server", "--stdio"], env, ROOT, evidence)
    report = {"version": version, "status": "running", "real_api_calls": 0,
              "model_turns": 0, "chatgpt_login_tested": False, "desktop_tested": False,
              "ui_approval_tested": False, "temporary_home": str(home),
              "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((LIGHT_ROOT / "mcp_agent_bus_light").rglob("*.py"))}}
    assert report["source_sha256"], "No light package sources found"
    try:
        init = client.request("initialize", {"clientInfo": {"name": "agent_bus_light_offline_test",
                              "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
        client.notify("initialized")
        app_listing = client.request("mcpServerStatus/list", {"serverName": "agent_bus_light",
                                     "detail": "toolsAndAuthOnly"})
        write_json(evidence / "app-discovered-tools.json", app_listing)
        thread = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": True,
                                "modelProvider": "offline_smoke", "model": "offline-smoke-no-inference",
                                "sandbox": "read-only", "approvalPolicy": "on-request",
                                "environments": []})
        tid = thread["thread"]["id"]
        listing = client.request("mcpServerStatus/list", {"threadId": tid,
                                 "serverName": "agent_bus_light", "detail": "toolsAndAuthOnly"})
        write_json(evidence / "discovered-tools.json", listing)
        server = next(x for x in listing["data"] if x["name"] == "agent_bus_light")
        tools = server["tools"]
        tool_names = list(tools) if isinstance(tools, dict) else [x["name"] for x in tools]
        assert "api_second_opinion" in tool_names, tool_names

        def call(name, args):
            return client.request("mcpServer/tool/call", {"threadId": tid, "server": "agent_bus_light",
                                  "tool": name, "arguments": args})

        def mock_count():
            wire = evidence / "mcp-wire.jsonl"
            if not wire.exists():
                return 0
            return sum(json.loads(line).get("event") == "mock_http"
                       for line in wire.read_text(encoding="utf-8").splitlines())

        blocked = call("api_second_opinion", {"question": QUESTION, "user_approved_api_send": False,
                                               "max_cost_usd": .01})
        assert blocked["isError"] is True and "approval" in json.dumps(blocked).lower(), blocked
        assert mock_count() == 0, "Unapproved call reached HTTP transport"
        report["blocked_without_approval"] = True
        approved_args = {"question": QUESTION, "user_approved_api_send": True,
                         "model": "gpt-6-luna", "max_output_tokens": 64, "max_cost_usd": .01}
        if "api_preview" in tool_names:
            preview = call("api_preview", {"question": QUESTION, "model": "gpt-6-luna",
                                           "max_output_tokens": 64, "max_cost_usd": .01})
            assert preview.get("isError") is not True, preview
            preview_payload = preview.get("structuredContent")
            if preview_payload is None:
                preview_payload = json.loads(preview["content"][0]["text"])
            request_hash = preview_payload.get("request_sha256") or preview_payload.get("approved_request_sha256")
            assert isinstance(request_hash, str) and len(request_hash) == 64, preview_payload
            assert mock_count() == 0, "Preview reached HTTP transport"
            approved_args["approved_request_sha256"] = request_hash
            report["preview_passed"] = True
            wrong_hash = call("api_second_opinion", {**approved_args, "approved_request_sha256": "0" * 64})
            assert wrong_hash.get("isError") is True, wrong_hash
            assert mock_count() == 0, "Mismatched approval reached HTTP transport"
            report["blocked_hash_mismatch"] = True
            low_budget = call("api_second_opinion", {**approved_args, "max_cost_usd": .00000001})
            assert low_budget.get("isError") is True, low_budget
            assert mock_count() == 0, "Insufficient budget reached HTTP transport"
            report["blocked_insufficient_budget"] = True
        allowed = call("api_second_opinion", approved_args)
        assert allowed.get("isError") is not True, allowed
        assert "BRIDGE_MOCK_OK" in json.dumps(allowed), allowed
        assert mock_count() == 1, "Expected exactly one in-memory mocked request"
        spent_out = call("api_second_opinion", approved_args)
        assert spent_out.get("isError") is True and "session budget" in json.dumps(spent_out).lower(), spent_out
        assert mock_count() == 1, "Exhausted process budget reached HTTP transport"
        report["blocked_exhausted_process_budget"] = True
        report.update(status="passed", discovered_tools=tool_names,
                      mocked_response_returned=True, initialize=init)
        results = {"blocked": blocked, "allowed": allowed, "spent_out": spent_out}
        if "api_preview" in tool_names:
            results.update(preview=preview, wrong_hash=wrong_hash, low_budget=low_budget)
        write_json(evidence / "call-results.json", results)
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        client.close()
        wire_file = evidence / "mcp-wire.jsonl"
        if wire_file.exists():
            wire = [json.loads(x) for x in wire_file.read_text().splitlines()]
            report["mcp_methods_observed"] = [x["request"].get("method") for x in wire if "request" in x]
            report["mock_http_calls"] = sum(x.get("event") == "mock_http" for x in wire)
        write_json(evidence / "summary.json", report)
    print(json.dumps(report, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--codex", default=shutil.which("codex") or "/opt/codex/bin/codex")
    p.add_argument("--evidence-dir", type=Path, required=True)
    p.add_argument("--mcp-wrapper", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.mcp_wrapper:
        wrapper(args.evidence_dir)
    else:
        run(args.codex, args.evidence_dir)


if __name__ == "__main__":
    main()
