# Independent image entry — candidate, no live image QA yet

This is **not** a tool automatically callable by chat/MCP. It does not turn Light
into an agent. It sends only a one-line text prompt to the official Images API,
not conversation history. No personal-image uploads, edits, image URLs, tools,
reference images, streaming, retries, custom endpoint, or proxy configuration.

## Offline first

From the standalone `light/` folder:

```sh
python -m mcp_agent_bus_light.image_generate --budget-usd 0.02
# Explicit legacy low-cost option, still OFFLINE by default:
python -m mcp_agent_bus_light.image_generate --model gpt-image-1-mini --quality low --size 1024x1024 --budget-usd 0.02
```

After an optional installation, `mcp-agent-bus-image` is the same entry point.
Enter a synthetic prompt when asked. Prompt input and the JSON preview are visible;
do not paste a credential there. The preview includes the complete payload,
fixed destination, output directory, model, size, quality, quantity, local estimate,
and your chosen USD threshold. Preview never collects a key, makes a network
request, or creates application records (Python may create bytecode caches).

The default model is `gpt-image-2.5-flare`, **preview-only**. Sunburst is also
explicitly selectable. Neither silently falls back to Mini. Their live path is
blocked before key collection or dispatch: the verified static docs expose rates
but not a complete output-token estimator matrix for all selected size/quality
combinations. We do not guess prices or treat an unknown estimate as zero.

Mini is a **deprecated, limited legacy option**, not a long-term default. Its
[announced retirement](https://developers.openai.com/api/docs/deprecations) is
2026-12-01. An independent hard stop blocks live use on/after that date even
if the price-review date is refreshed. Mini live mode exists only for a future
authorized single-request smoke test before both expiry boundaries. It
requires `--live`, an interactive terminal, a positive `--budget-usd`, and exact
hidden confirmation of the payload SHA-256 plus the USD threshold. The key is
then collected with fail-closed hidden input (no echo fallback), retained only
in process memory, and never placed in arguments, environment variables, files,
logs, or error details. Memory is not securely zeroized. This implementation
currently allows live file saving only on POSIX; Windows preview works, while
Windows live is blocked until private ACL handling is implemented.

**No real image request has been authorized or run for this candidate.** A real
smoke test requires the user's chosen model, synthetic prompt, dimensions,
quality, and specific single-attempt budget. Text-API success is not image QA.

## Costs and capabilities (official docs checked 2026-10-29)

[Image guide](https://developers.openai.com/api/docs/guides/image-generation)
recommends Sunburst/Flare for new integrations. Both use direct Images API:
text input $5/M tokens, image input $8/M, image output $30/M; cached rates do
not apply to direct Images calls. Quality choices are low/medium/high/xhigh/max.
This candidate restricts size choices to 1024x1024, 1024x1536, 1536x1024.
Organization verification/access may be required; no access probe is performed.

[Mini model pricing](https://developers.openai.com/api/docs/models/gpt-image-1-mini):
text input $2/M; output-image USD estimates below exclude prompt input:

| Quality | 1024x1024 | 1024x1536 | 1536x1024 |
|---|---:|---:|---:|
| low | .005 | .006 | .006 |
| medium | .011 | .015 | .015 |
| high | .036 | .052 | .052 |

The Mini local estimate adds one text token per UTF-8 prompt byte at $2/M to
that table. This is a rough allowance, not exact tokenization or a provider
maximum. Pricing may change. Live use fails closed after the local review expires on
2026-11-28 (or before its review date); refreshing that date requires rechecking
the official prices, not merely editing a date to bypass the guard. **`--budget-usd` is a local estimate threshold,
NOT a provider hard cap, not a lifetime/cumulative spend limit, and not proof
that a request will be free.** Different payloads in the same directory each
receive an independent per-attempt check, so their sum can exceed that threshold.
There is no shared/session/account-level spend counter in this entry. `n=1` and explicit size/quality are always sent.

[ChatGPT API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)
cover Images generation/editing for the linked organization. Purchased credits
can be consumed after free credits run out. The entry cannot inspect balances,
restrict billing to free credits, or validate a particular key's entitlement.

## Files, unknown outcomes, and duplicate protection

Default output: `~/.local/state/mcp-agent-bus-light/images/`. A different private
directory may be explicitly supplied. Existing directory permissions are never
changed. Destination must be owned by the current user, mode 0700, and contain
no symlink ancestors. New files are mode 0600, created exclusively: never
replace existing images or records. This is same-user local storage, not a
sandbox against a malicious same-user process racing filesystem changes.

Before dispatch, `<payload-sha256>.attempt.json` is written and file/directory-fsynced with
`UNKNOWN`, estimate, threshold and endpoint; it never contains the key or prompt.
It remains as an immutable reservation. A successful save adds an independent
`.success.json` marker. On timeout, disconnect, HTTP error (including rejection),
invalid response, Ctrl-C, decode/save failure or uncertain completion, the
UNKNOWN reservation is retained, with no refund or automatic retry. Its presence
blocks an identical payload in that directory across ordinary process restarts.
This is **not a power-loss/crash-consistency guarantee**: newly created parent
directory entries are not all fsynced, and filesystem/hardware durability differs.
Do not infer zero duplicate risk after power failure, directory loss or restore. Changing the
output directory bypasses this local duplicate ledger; do not use it to retry
an uncertain request. The CLI deliberately has no retry/clear-ledger switch.
Inspect provider billing and any existing output manually before deciding what
to do next. A local UNKNOWN is not evidence that the provider did no work.

Responses are bounded; only one base64 PNG is accepted, never a fetched URL.
PNG checks require valid four-letter chunk types (including the reserved bit),
known critical chunks, valid CRCs and structural ordering: IHDR first, optional
PLTE before image data, consecutive IDAT chunks, and an empty IEND last. Known
ancillary positioning and singleton rules are checked. Legal unknown ancillary
chunks are permitted as opaque bytes. **Metadata, EXIF, text, color profiles and
provenance/C2PA are preserved byte-for-byte**, not stripped, extracted, displayed
or independently verified. Never assume the image is metadata-free; no metadata
sanitization or authenticity guarantee is made. No embedded ancillary payload is
decompressed or interpreted. APNG animation payloads are likewise not decoded;
validation concerns the default PNG image, not full animation conformance.

Pixel validation currently supports only 8-bit RGB/RGBA without interlacing,
checking decompressed byte length and row filter values. Indexed, grayscale,
16-bit and interlaced pixels need different row/bit/pass calculations, which this
small validator does not implement yet; those outputs fail closed and may
already have incurred cost. Chunk rules reference the
[PNG specification](https://www.w3.org/TR/png-3/#5ChunkOrdering).
The file is not executed or automatically
opened/uploaded. These checks are structural, **not visual/content quality QA**.
Unsupported valid PNG variants fail closed and can still have incurred cost.

## Offline tests

```sh
python -m unittest discover -s tests -p test_image_generate.py -v
python -m unittest discover -s tests -v
python scripts/verify_offline.py
```

Tests use synthetic keys, fake HTTP and synthetic PNG bytes, with network
connections forbidden. They cover preview, explicit model allowlist, unknown
pricing lockout, exact confirmation cancellation, no noninteractive live mode,
fixed HTTP host/path, no redirects/retries, duplicates, UNKNOWN persistence,
secret suppression, malformed image responses, private paths and no overwrite.
Real provider acceptance, output quality, billing and entitlements remain untested.
