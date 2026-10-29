# Attribution and dependency boundary

This independent adapter is original integration code in
[SamZebrado/mcp-agent-bus](https://github.com/SamZebrado/mcp-agent-bus), under the
repository's existing MIT notice reproduced unchanged in `LICENSE`.
It does not copy the Light/Dual Lane provider implementation and does not contain
an agent/model loop. The fake ACP process and validation scripts are original
synthetic test code, not real agents.

## Reused implementation

The package depends on the official **agent-client-protocol Python SDK 0.12.1**,
licensed **Apache-2.0**. It uses `acp.connect_to_agent`, request/response models,
and the documented message-Transport seam; SDK code is not vendored here.

- Repository/tag: https://github.com/agentclientprotocol/python-sdk/tree/0.12.1
- Reviewed commit: `e668ed9e0034f5076749a6d74c5cec40ab610010`
- License: https://github.com/agentclientprotocol/python-sdk/blob/0.12.1/LICENSE
- API factory: https://github.com/agentclientprotocol/python-sdk/blob/0.12.1/src/acp/core.py
- Transport seam: https://github.com/agentclientprotocol/python-sdk/blob/0.12.1/src/acp/_transport.py
- Direct SDK dependency is Pydantic; this adapter pins **Pydantic 2.13.4** for the
  tested environment. Pydantic is MIT-licensed and brings its own runtime
  dependencies/notices. No SDK telemetry, HTTP or WebSocket extras are requested.
  https://github.com/pydantic/pydantic/blob/v2.13.4/LICENSE

Local validation used a separately materialized, Git-blob-verified official SDK
checkout with existing Pydantic. This is not a claim that that checkout was a
PyPI-installed wheel. Published source declares the exact dependency versions;
normal installation obtains those dependencies separately. Do not substitute an
unreviewed similarly named package or silently upgrade the protocol layer.

## Original protective wrapper

Our wrapper implements bounded framing, strict JSON/envelope checks, fixed
reverse-request denial, state/approval policy and process cleanup around the SDK.
It does not copy or patch SDK source. These additions address the reviewed SDK's
unbounded long-line reassembly, raw diagnostic logging and `_meta`-to-handler
kwargs merge for this deliberately restricted interface. They are not a claim
that the SDK is unsafe for every use case or that this wrapper provides complete
ACP conformance.

## Design references, no copied implementations

- ACP **v1** normative documentation. v2 remains a draft and is not implemented:
  https://agentclientprotocol.com/protocol/v1/initialization
  https://agentclientprotocol.com/protocol/v1/prompt-turn
- MCP stdio/tool/cancellation/progress documentation; no MCP SDK is bundled:
  https://modelcontextprotocol.io/specification/2025-06-18
- `theorionic/mcp-acp-bridge`, reviewed revision
  `aa81724caaad13d0d2deb098f71a4523439a8a2f`: package/README declare Apache-2.0,
  but no standalone license file was present in the reviewed tree. No code copied.
  https://github.com/theorionic/mcp-acp-bridge/tree/aa81724caaad13d0d2deb098f71a4523439a8a2f
- `creverse-ai-lab/agent_gateway`, revision
  `9669951151ae9650731e2c1d23e94104056f6e2d`, Apache-2.0: reviewed for lifecycle and
  permission tradeoffs, not copied or installed.
  https://github.com/creverse-ai-lab/agent_gateway/tree/9669951151ae9650731e2c1d23e94104056f6e2d
- DeepSeek Harness is a possible future ACP agent, **not a dependency or a tested
  real integration**. No Harness source, agent loop, shell tools or credentials
  are included: https://github.com/deepseek-ai/deepseek-harness

Protocol compatibility, project references and dependency use do not imply any
upstream endorsement or permission to bypass provider billing/access rules.
