# Attribution and provenance

## Project code

`mcp-agent-bus-light` is an optional companion to
[SamZebrado/mcp-agent-bus](https://github.com/SamZebrado/mcp-agent-bus).
The existing task bus remains a separate, local coordination tool; the light
package does not replace its default entry point or run its task queue.

The Responses API, terminal-chat, and MCP code was adapted from the
owner-supplied **Dual Lane API Bridge v0.1.0** prototype and its subsequently
developed **v0.2.0 safety baseline**. The light integration adds its own entry
points, packaging, capability boundaries, and regression tests.

The supplied v0.1.0 archive contained **no standalone LICENSE file or license
declaration in its plugin manifest**. Its README and handoff explicitly listed
license selection/licensing as future work. We therefore do not describe that
archive as an independently MIT-licensed third-party upstream, or infer an
upstream license from its availability.

The existing repository's MIT notice, including its copyright attribution to
Sam Zebrado, is preserved unchanged in [the repository license](https://github.com/SamZebrado/mcp-agent-bus/blob/main/LICENSE) and
[the light package license](LICENSE). This provenance note does not alter those
terms or assign a license to external reference projects.

## External references, not bundled implementations

- **DeepSeek Harness:** official documentation and selected source were reviewed
  for agent-runtime reuse, its ACP automation server, provider routing, and
  lifecycle behavior. The reviewed source identifies itself as
  `0.2.2-alpha.2`; see the
  [reviewed revision](https://github.com/deepseek-ai/deepseek-harness/tree/f3803c96974ad4a94195fb97e5c637e9c2a3afe3)
  and its [MIT license](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/LICENSE).
  **No Harness source or executable is copied, vendored, installed, or launched
  by this package.** The disabled backend and `MockAcpContract` are original
  test-scaffolding code, not a port of Harness's test fixture or agent loop.
- **Agent Client Protocol (ACP):** the
  [official protocol documentation](https://agentclientprotocol.com/) and
  [official TypeScript SDK](https://github.com/agentclientprotocol/typescript-sdk)
  informed the proposed integration contract. The SDK has its own
  [Apache-2.0 license](https://github.com/agentclientprotocol/typescript-sdk/blob/main/LICENSE).
  No ACP SDK code is bundled or required. In-memory mock tests do not establish
  real ACP transport or Harness compatibility; the live Harness backend remains
  unavailable.
- **Model Context Protocol (MCP):** the text-only stdio interface follows a small
  subset of the [official protocol](https://modelcontextprotocol.io/). No MCP SDK
  code is bundled or required, and this is not a claim of complete protocol
  coverage.
- **OpenAI Responses API:** request behavior is informed by
  [official API documentation](https://developers.openai.com/api/docs/guides/text).
  No OpenAI client SDK is bundled. Use of documented interfaces does not imply
  endorsement, account/model access, promotional-credit eligibility, or verified
  live billing behavior.

## Dependency boundary

The light package uses Python's standard library and adds **no third-party
runtime dependencies**. References above are not installation instructions,
dependency declarations, or an automatic-download path. Optional runtime
integration would require a separate implementation and dependency/license
review; it is not enabled by these attributions.
