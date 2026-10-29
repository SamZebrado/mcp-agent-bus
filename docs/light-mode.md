# Optional Agent Bus Light

The complete user guide, examples, license and standalone packaging live in
[`../light/`](../light/README.md). Copy that folder alone for zero-install usage,
or install its `pyproject.toml` into a fresh virtual environment. The root package
can alternatively install both bus and light from the same source; do not install
both distributions in one environment.

## Separation from the stable bus

Light uses a direct stdio MCP request path. It does not enqueue short API calls,
start workers, acquire/reclaim leases, record prompts in the task bus, or enable
audit logging. Existing core source, schema, tests, entry points and defaults are
unchanged. The existing dashboard remains a read-only view of bus state and is
not a key-entry form. There is no separate website/page demo in this source tree.

Do not route billable work through automatic expired-lease reclaim. The current
bus has no execution fencing token or proof that a prior external request
stopped; send idempotency is not exactly-once provider billing. A future paid
worker integration needs separate lease, cancellation, unknown-outcome and
reconciliation design rather than blindly retrying.

## Maintainer checks

```sh
bash run_smoke.sh
python scripts/verify_light_offline.py
# Requires preinstalled pip/setuptools/wheel; does not download dependencies.
python scripts/verify_light_wheel.py --evidence-dir /new/empty/root-wheel-evidence
python scripts/verify_light_wheel.py --standalone --evidence-dir /new/empty/light-wheel-evidence
# Optional actual Codex CLI discovery/calls with fixture HTTP and no model turn:
python scripts/verify_light_codex.py --codex /absolute/path/to/codex --evidence-dir /new/empty/cli-evidence
```

Use a fresh empty evidence directory each time. The CLI script creates its own
isolated HOME/CODEX_HOME, fixed fake key and unreachable model-provider fixture;
it never reads a pre-existing login or starts a model turn. Its raw reports are
local validation artifacts, not content to publish to the source repository.
Successful discovery/calls do not establish that a signed-in UI enforces the
intended human approval or that a promotional balance pays for a live request.

The separate light CI workflow tests copied-folder execution on Linux, Windows
and macOS with Python 3.10/3.13. A workflow definition is not a successful run;
inspect the checks for the exact commit before claiming OS results.

## Reuse rather than a new agent runtime

The light tool deliberately stops at one explicitly approved text request (or a
human-operated separate chat). Use an existing agent for shell/tools/multi-step
work. The separate [`acp_bridge/`](../acp_bridge/README.md) now provides a restricted
protocol utility with local fake ACP validation, not tied to Harness or the queue.
Light's `harness_acp` code remains a fail-closed unavailable backend plus in-memory
contract tests; it is not a real Harness integration. See the standalone
guide and [`ATTRIBUTION.md`](../light/ATTRIBUTION.md) for source provenance and
research references.

## Separate image CLI

`mcp-agent-bus-image` (or `python -m mcp_agent_bus_light.image_generate`
from the copied folder) provides a separate human-operated image entry. It is
not an MCP tool and never acquires bus leases or invokes an agent loop. Read
[the image guide](../light/IMAGE_GENERATION.md) before use: default preview only,
new models live-disabled pending verified estimation, limited Mini expiry,
per-attempt estimate rather than a provider/cumulative cap, directory-scoped
UNKNOWN reservations, and unchanged PNG metadata/provenance. No real image
request is part of the offline validation. Root and standalone wheel probes
verify this entry imports, shows help and previews without network/files/key reads.
