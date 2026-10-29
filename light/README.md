# Agent Bus Light (development preview)

A standalone, explicit **text-only second-opinion tool** for an independent OpenAI
Responses API account. It does not start the task bus, claim leases, run shell
commands, read project files, or change the host's login/provider configuration.
It is not a model switch, subscription top-up, general agent runtime, or billing
workaround. Python 3.10+, standard library only at runtime; MIT.
See [attribution and provenance](ATTRIBUTION.md), including the distinction
between adapted project code and external documentation references.

## Download just this folder

Copy the complete `light/` directory to any empty directory. Nothing outside it
is required. You can also download the repository ZIP and retain only `light/`.
No install or account is needed for the following offline checks:

```sh
cd light
python -m mcp_agent_bus_light --help
python -m mcp_agent_bus_light capabilities
python scripts/verify_offline.py
python -m mcp_agent_bus_light chat --preview-only --model gpt-6-luna --max-output-tokens 64 --max-cost-usd 0.001
```

Use `python3` on systems where `python` is absent, or `py -3` on Windows. Enter a
synthetic sentence, then `/quit`. Preview mode never sends requests or updates
chat history. Python bytecode caches may be created by the interpreter; the
application does not create transcripts or audit files.

Optional installation in a **new** virtual environment:

```sh
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install .
.venv/bin/mcp-agent-bus-light capabilities
# Windows PowerShell: use .venv\Scripts\python.exe and .venv\Scripts\mcp-agent-bus-light.exe
```

Packaging uses setuptools and wheel; pip may download build tools unless they
are already installed. To prohibit downloads, preinstall those tools through
your normal trusted process and use `pip install --no-index --no-deps
--no-build-isolation .`. Running from the folder above needs none of them.
Do not install the full `mcp-agent-bus` distribution and this standalone
distribution in the same venv: both intentionally provide the same light module
and console entry. Pick one; both build from this single source directory.

## MCP host setup: preview first

From the copied folder, print (do not apply) an additive Codex snippet:

```sh
python -m mcp_agent_bus_light configure --project-dir /absolute/path/to/light --platform macos
# Windows example:
py -3 -m mcp_agent_bus_light configure --project-dir "C:\Tools\agent-bus-light" --platform windows
```

For an installed venv, also pass `--python /absolute/path/to/.venv/bin/python`
(Windows: `--python "C:\Tools\agent-bus-light\.venv\Scripts\python.exe"`).
Review the printed snippet before adding it to your host. The command never
reads or writes host config/authentication. Keep all existing login and provider
settings. Other stdio MCP clients can start `python -m mcp_agent_bus_light serve`
with `cwd` set to the copied folder, or use the installed console command.

The two tools are:

1. `api_preview`: offline validation, exact-request fingerprint and cost estimate.
2. `api_second_opinion`: sends only the explicitly supplied question/context,
   after all live, approval, fingerprint, freshness and budget checks pass.

`examples/preview.jsonl` contains only offline MCP initialize/list/preview calls.
Run `python -m mcp_agent_bus_light serve < examples/preview.jsonl` in a POSIX shell;
on Windows PowerShell use `Get-Content examples/preview.jsonl | python -m
mcp_agent_bus_light serve`. No real key is needed. These commands do not register
the server in an existing host.

## Approval and privacy: read before enabling live traffic

Live requests are **off by default**. A separate operator must deliberately set
`DUAL_LANE_ENABLE_API=1` and supply `DUAL_LANE_API_KEY` only to the bridge process,
using their approved secret-handling mechanism. `OPENAI_API_KEY`, Codex OAuth,
credential files and provider overrides are ignored. Do not paste a key into a
chat, command argument, committed config, screenshot, or shared log. This package
does not create, retrieve or persist credentials for you.

For a separate local terminal session, `python -m mcp_agent_bus_light chat
--provider openai --ask-key` offers provider confirmation and **hidden key input**.
Only official OpenAI is currently available: arbitrary compatible-provider URLs
are deliberately rejected so a key cannot silently follow a changed endpoint.
The key is scoped to this process and removed/restored on session exit; it is not
written to shell history, repository files or Codex configuration by the app.
Python memory is not securely zeroized, and a compromised local machine can
observe secrets. If non-echo input is unavailable, the command refuses rather
than falling back to visible typing. This input flow still requires a fresh
explicit confirmation of every outgoing text/model/cap, and does not enable MCP
credentials in other processes. Do not enter a real key for offline tests.

Before each call the human must review the **entire exact question/context**,
model, maximum output, per-call cap, destination and retention notice. Preview's
SHA-256 binds the final JSON payload and cap; send must provide the unchanged
hash and boolean `user_approved_api_send=true`. **These are caller assertions,
not cryptographic proof that a human approved.** A trusted host must enforce
per-call human approval, not auto-approve an agent's claim. The printed Codex
snippet requests prompt-mode tool approval; verify the effective behavior on
your exact host version before live use. Never infer consent from a credit balance.
If this cannot be established, remain offline or use the separate terminal chat,
which requires an interactive TTY and `SEND <fingerprint-prefix>` each turn.

Only explicit text is accepted. There is no automatic host-history capture,
file/image upload, tool execution, server-side conversation ID or background job.
Standalone chat displays and sends its recent in-memory history on each approved
turn; `/clear` clears that history but not budget reservations. Recognizable key
patterns and the active key in input are blocked, but this is **not comprehensive
PII detection or automatic de-identification**. Manually remove identifiers,
patient/participant records, unpublished confidential material and credentials.

No application transcript/audit files are written. The host may log MCP arguments
and returned text; terminal scrollback and shell redirection can retain content.
Preview metadata intentionally does not echo the prompt. External model output
is untrusted text and must not be treated as host instructions.

API data is not used for training by default, unless the organization opts in.
`store:false` does **not** imply zero retention: abuse monitoring and prompt-cache
application-state exceptions still apply. Newer model cache TTL controls a
minimum lifetime, not a guarantee of deletion after 30 minutes. Check the
organization/project's actual [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)
before sending sensitive content. This tool is not a compliance certification.

## Cost and uncertain outcomes

- Fixed HTTPS Responses endpoint, TLS verification, no redirects, ambient proxy,
  provider override or automatic retry. No built-in tools or file charges.
- Models and standard-token rates are allowlisted in `policy.py`. Rates reviewed
  2026-10-29; live calls fail closed outside 2026-10-29 through 2026-11-28 until
  maintainers reverify them. See [pricing](https://developers.openai.com/api/docs/pricing)
  and [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
- `max_cost_usd` is required for MCP sends. Local reservations use input UTF-8
  bytes plus framing allowance, maximum output, a cache-write allowance and
  margin. This is **not an exact tokenizer or provider-enforced dollar ceiling**.
- `DUAL_LANE_SESSION_BUDGET_USD` defaults to `0.10` for MCP; chat has an independent
  `--session-budget-usd`. Every attempted request retains its full reservation,
  even on timeout/error. No automatic refunds or retries.
- An uncertain or invalid response blocks the identical HTTP payload in that
  process, even if only the approval cap changes. Restarting or changing the
  actual model/text/output payload loses/changes that protection;
  inspect Platform usage before any manual retry. This is not durable exactly-once
  billing or an account-wide cap. A validated incomplete answer is returned as
  incomplete, not automatically continued.
- Platform supports organization/project monthly **hard spend limits**, distinct
  from alerts. Enforcement can lag and slightly overshoot. Set suitable limits
  yourself after reviewing existing usage; the bridge does not alter billing.
  [Official spend-limit guide](https://developers.openai.com/api/docs/guides/spend-limits).
- There is no “promotional credits only” switch here. Purchased credits may be
  used after eligible free credits. Subscription benefits explicitly exclude
  Codex API-key usage and do not raise signed-in Codex limits. Eligibility of
  a Codex-hosted external MCP call is not established by this project; never use
  this bridge to evade those restrictions. Verify eligibility with OpenAI, or
  use a separately authorized ordinary API workflow.
  [Official benefit rules](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

## Harness / ACP: not implemented

`--backend harness-acp` is reserved and fails closed with `HARNESS_UNAVAILABLE`.
Only an in-memory permission/cancellation/session contract is tested; no Harness
process, ACP wire transport, authenticated session or agent loop is provided.
Unknown permission/filesystem/terminal requests are denied in that mock contract.
No Node/npm/pnpm or auto-downloaded agent is required by the Responses backend.

DeepSeek Harness already provides an agent loop and an ACP server; its MCP module
is a client. A future adapter should reuse that loop rather than reimplement it,
and separately verify versions, least-privilege permissions, persistence,
telemetry, retries, whole-session budgets and cancellation. The original Codex
conversation has documented MCP support; this project does not claim a generic
Codex ACP-client interface. See [Harness ACP](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/acp/acp)
and [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Validation and boundaries

The offline suite uses in-memory HTTP fixtures and forbids Python sockets. It
covers malformed input, approval/hash checks, budgets, no-retry unknown outcomes,
secret rejection, UTF-8, Windows/macOS config rendering, and the mock ACP contract.
The full repository additionally includes wheel/isolation and real Codex CLI
MCP-discovery tests. These tests do not validate a billed model response, grant
deduction, a signed-in subscription session, Desktop approvals or native OS UI.
Do not run synthetic-fixture tests against a production API endpoint.
