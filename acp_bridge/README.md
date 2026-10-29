# Standalone ACP-to-MCP adapter (development preview)

```text
MCP host → explicit approval + static operator profile → ACP v1 client → existing ACP agent
```

Copy this **entire `acp_bridge/` folder** to use it independently of Agent Bus or
Light. This tool reuses the official Python ACP SDK; it does not implement an
agent loop, bundle Harness, enqueue tasks, or reuse bus database/leases.
It exposes an ACP agent through MCP — not the reverse MCP-over-ACP SDK helper.
See [source attribution and licenses](ATTRIBUTION.md).

**Default: no external process is enabled.** Only local fake ACP subprocesses
have been validated. Real Harness, other real ACP agents, signed-in Codex UI and
Desktop approval behavior are not verified. Do not treat a protocol test as
permission to launch a paid agent or transmit private files.

## Install and inspect

Python 3.10–3.14; use a **new virtual environment without telemetry extras**:

```sh
cd acp_bridge
python -m venv .venv
# macOS/Linux; Windows uses .venv\Scripts\python.exe
.venv/bin/python -m pip install .
.venv/bin/python -m agent_bus_acp capabilities
.venv/bin/python -m agent_bus_acp serve --help
```

Installation normally downloads the pinned official ACP SDK/Pydantic and build
tools. No dependency is automatically downloaded during import, help, preview or
serve. For offline installs use a separately verified local wheelhouse. This is
not a zero-dependency package: the sibling Light tool remains stdlib-only.
Imports/help/capabilities work without the SDK; actual approved execution needs
it. A source checkout runs with `python -m agent_bus_acp ...` from this folder.
Do not install this into an environment that includes Logfire/OpenTelemetry:
execution refuses those ambient tracing extras rather than risk exporting text.

## Safest first test: synthetic local process

Create an empty scratch workspace and a private state directory of your choice.
On POSIX the state directory must be owner-only (`0700`); on Windows the operator
must ensure an owner-only ACL. Do not use your real project or credential home.

```sh
python examples/make_mock_profile.py --cwd /absolute/empty/scratch > mock-profile.json
python -m agent_bus_acp serve --profile-file /absolute/path/to/mock-profile.json \
  --state-dir /absolute/private/state --allow-agent-process
```

The first command prints configuration; shell redirection creates the file you
selected. The example uses only fixed synthetic responses and explicitly forbids
network and child-process creation inside the fake agent. Other scenarios are
`callbacks`, `cancel` and `disconnect`. This is a developer MCP server on stdin /
stdout, not a chat prompt. Configure your MCP host to start this same command,
with absolute paths. No API key or existing login is required for the fake test.

The four MCP tools:

1. `acp_preview(profile, text)` — offline metadata/fingerprint, no child start.
2. `acp_prompt(profile, text, user_approved_agent_run, approved_request_sha256)` —
   one new process/session, one approved text prompt; final text returned.
3. `acp_status()` — active run ID/state, no transcript or credentials.
4. `acp_cancel(run_id)` — requests cancellation. It returns
   `cancel_confirmed:false`; only the original prompt response with
   `stopReason:cancelled` confirms ACP cancellation.

Always show the human the exact text and operator-selected profile, timeout,
output/call limits and external-effects warning before `acp_prompt`. A boolean
and preview hash are caller assertions/content checks, **not proof of human
identity, one-time consent or an expiry-enforced approval token**. Use a trusted
host's real per-call approval UI. No approval means zero SDK import, state write,
child launch or prompt. Configuration flags are not blanket consent to each text.

## Static profile and boundaries

Only the operator-supplied JSON file chooses the executable, argv, cwd, permitted
environment variable **names**, and limits. MCP callers cannot pass commands,
extra args, paths, environment values, URLs, tools, auth or nested MCP servers.
A minimal profile has `name`, absolute executable `argv`, and absolute `cwd`.
Defaults: `pass_env:[]`, `timeout_seconds:30`, `max_output_bytes:65536`, `max_calls:1`.
A maximum of one run is active; other prompts fail busy, not queue.

The child gets a fresh temporary HOME/USERPROFILE/XDG directory and a minimal
PATH. It does not inherit the normal credential home or environment wholesale.
Only explicit `pass_env` names are forwarded; never put secrets in argv/profile
files. No authentication/login flow is initiated by the bridge. For a real
agent, the operator must separately review its provider, storage, telemetry,
permissions, authentication and cost controls. Do not configure credentials or
launch a real agent as a side effect of this example.

**ACP permissions are advisory; this bridge is not an OS sandbox.** Client-side
permission requests always receive `cancelled`; filesystem, terminal, elicitation
and unknown reverse requests get fixed errors. But an external agent may use its
own tools to read/write files, execute commands, access the network or bill a
provider without requesting those client capabilities. The `acp_prompt` tool is
therefore marked potentially destructive. Use an independently restricted OS
container/sandbox and provider policy when permitting a real agent. A fresh HOME
and denied callbacks do not constitute that sandbox.
[Official ACP security considerations](https://agentclientprotocol.com/protocol/v1/security).

## Privacy, streaming and cancellation

- Text only; no automatic host-history, file/image capture, nested MCP servers,
  session loading/resumption, OAuth or agent mode/model changes.
- The bridge writes no prompt/output transcript and discards child stderr. The
  host, agent and provider may retain data; do not promise secrecy/zero retention.
  Manual de-identification and external-provider review remain necessary.
- SDK raw logs are disabled during a run. Incoming metadata is rejected; wire
  frames, total bytes, frame count and accumulated output are bounded. This
  restricted ACP v1 subset intentionally fails closed on incompatible peers.
- ACP text chunks are accumulated; MCP progress reports only byte counts when
  the caller provides a progress token, at most 10 times/second. The final result
  contains text. This is **not a claim of user-visible token streaming** in Codex.
- Legitimate idle metadata and post-completion text are ignored, not treated as
  active-turn output. Wrong active session, malformed data and over-limit output
  poison the run. Tool/plan/thought updates are not executed or presented as
  instructions.
- MCP cancellation notifications and explicit `acp_cancel` send ACP
  `session/cancel`. Last chunks before the original cancelled response remain
  valid. A sent cancellation, local process kill or timeout is **not proof** that
  provider work stopped or became free.
- Startup initialize/session creation each have a 10-second bound; the profile
  timeout applies to the prompt, followed by a bounded cancellation/cleanup
  grace period. Only an advertised `session/close` capability is used. Direct
  child cleanup cannot guarantee that grandchildren or remote work stopped.

## Unknown outcomes and costs

There is **no enforceable dollar budget in this protocol bridge**. An ACP agent
can make multiple model/tool calls, retry or compact. ACP usage events are not
billing proof. Enforce spending/retry/model controls at the provider/agent
boundary, and verify them before any real use. Do not use subscription benefits
as a bypass for disallowed Codex API-key usage.

The private state directory contains only a small SQLite quarantine/call counter:
keyed request fingerprints, status and attempt counts, never text or API keys.
This metadata-only safety ledger is not bus audit logging or a task queue.
A pending/unknown fingerprint survives a bridge restart; there is no automatic
reconnect/resume/replay. Editing only the profile name, timeout or limits cannot
bypass it. Call counts are durable per executable/argv/cwd/env-name identity and
include startup failures; known completion does not refund a call. Raising an
operator call-count limit does not clear unknown quarantine.

This is not global exactly-once billing: changing the actual command/text or
choosing/deleting a different state directory changes the protection scope.
Never clear state or retry after uncertainty without operator/provider
reconciliation. Unconfirmed child cleanup disables further work and leaves a
persistent block. File permissions/ACLs, disk snapshots and host security remain
operator responsibilities; the bridge offers no automatic reset command.

## Tests and honest compatibility

```sh
python scripts/verify_offline.py
# Optional actual Codex CLI fixture check; own isolated HOME, no model turn:
python scripts/verify_codex.py --help
```

Tests use an actual SDK connection and local fake ACP pipes, not real models.
They cover bounded frames, denied capabilities, authorization, cancellation
races, persisted unknown outcomes, subprocess cleanup and no plaintext ledger.
CLI tests distinguish actual MCP discovery/calls from mock ACP output. Official
ACP agent-side conformance tests would not establish conformance of this client.
Native OS CI, package/install tests and real-agent checks must be reported
separately for the exact commit. Current limitations are deliberate: no agent
runtime, automatic resuming, broad permissions, telemetry, HTTP server, or
credential management. Use an existing agent for richer work, not new complexity
inside this bridge.
