# Veedcrawl Plugin

Native [Veedcrawl](https://veedcrawl.com) integration for Hermes. Veedcrawl is a
video-intelligence REST API that turns public YouTube / TikTok / Instagram /
X / Facebook URLs into metadata, transcripts, and structured AI extractions.

This plugin exposes **8 agent tools** against the official Veedcrawl REST API.
Discovery tools embed **monthly persist** via `kol-ops-bridge` `fetch_with_persist`
(one tool call = cache lookup + API + full JSON blob). Async jobs
(`/v1/transcript`, `/v1/extract`) are polled inside the client.

**Production discovery uses these plugin tools, not MCP.**

## Tools

| Tool | Endpoint(s) | Cost | Persist envelope |
| --- | --- | --- | --- |
| `veedcrawl_account` | `/v1/me`, `/health` | 0 | No |
| `veedcrawl_metadata` | `GET /v1/metadata` | 0 | Yes |
| `veedcrawl_search_social_videos` | `POST /v1/search` (+ poll) | ~5–15 / platform | Yes |
| `veedcrawl_instagram_profile` | `GET /v1/instagram/profile` | 0 | Yes |
| `veedcrawl_profile` | `/v1/{instagram,tiktok}/profile` | 0 | Yes |
| `veedcrawl_transcript` | `POST /v1/transcript?url=...` (+ poll) | 1 native / 5 whisper | Plugin TTL cache only |
| `veedcrawl_extract` | `POST /v1/extract` (+ poll) | 10 | Yes |
| `veedcrawl_job` | `GET /v1/{transcript,extract}/{job_id}` | 0 | No |

Key management (`/v1/keys`) is intentionally **not exposed** — keys are an
operator concern, not an agent concern.

## Authentication

The plugin reads the API key from one of (in order):

1. `VEEDCRAWL_API_KEY` env var
2. `X_API_KEY` env var (matches Veedcrawl's MCP convention)

Get a key at <https://veedcrawl.com/login> (50 free credits, no card required).

```bash
export VEEDCRAWL_API_KEY=vc_live_xxxxxxxxxxxxxxxx
```

When no key is configured, the tools remain registered but their `check_fn`
gate prevents dispatch — `hermes tools` will list them as unavailable.

## Guardrails

**OpenAI function schema shape** (`as_function_schema` in `tools.py`): each tool
registers `{name, description, parameters}` — not a bare parameters object.
Bare objects were sanitized to empty `properties: {}`, which made models think
veedcrawl tools had no parameters (and try `terminal` JSON workarounds).

**Pre-dispatch argument validation** (`hooks.py` + `_internal/arg_validate.py`):
incomplete tool calls (empty `{}`, missing `username`/`q`/`url`, etc.) are
blocked in `pre_tool_call` before handlers run. The model receives an example
JSON payload instead of a generic `bad_request`. JSON schemas also mark
required fields (`oneOf` / `allOf`) so capable models see constraints up front.
This does not replace session guards (e.g. `kol-email-discover:*` blocks all
`veedcrawl_*` — email discovery must use `WebSearch` / `browser_*`).

The client enforces three protections so agents cannot accidentally burn
credits or hammer the API:

- **Credit guardrail** — before any paid call (`extract`, `transcript` with
  `mode=generate`/`auto`), the client checks `/v1/me` (60 s TTL cache) and
  refuses if `creditsRemaining < cost × safety_factor` (default `2`). The
  threshold and factor are read from `plugin.yaml`'s `config` block, never
  from agent input.
- **Rate-limit recovery** — `429` responses are retried exactly once after
  sleeping until `X-RateLimit-Reset` (+ jitter). A second `429` surfaces as a
  structured `rate_limited` error.
- **Monthly persist cache (discovery)** — `metadata`, `search`, `profile`,
  and completed `extract` responses are stored under
  `$HERMES_HOME/kol-ops-bridge/veedcrawl_cache/` (SQLite + blobs, month =
  Asia/Shanghai `YYYY-MM`). Tool responses include `cache_hit`, `persisted`,
  `blob_ref`, `storage_ref` (always set when persisted — blob path or
  `sqlite:{month}:{key}`). Optional `identity_id` writes `identity.veedcrawl_*`
  CAL index facts. Extract blobs include `api_response` (full completed poll JSON).
  See `agent_prj/docs/kol-veedcrawl-integration.md`.
- **Short TTL plugin cache** — still used inside the HTTP client for
  `metadata` / `profile` (24 h / 6 h) and completed async jobs at
  `~/.hermes/cache/veedcrawl/`. Discovery handlers bypass this when the monthly
  cache misses (`force_refresh` on API fetch).

## Example

```python
# Inside an agent prompt — synchronous semantics, polling handled internally
{
  "tool": "veedcrawl_extract",
  "args": {
    "url": "https://www.instagram.com/reel/ABC123/",
    "prompt": "Score this creator against the persona JSON below…",
    "schema": {"type": "object", "properties": {"score": {"type": "number"}}}
  }
}
```

## Async escape hatch

Pass `wait: false` to receive `{"job_id": ..., "status": "queued"}` immediately
and re-call the same tool with `job_id` (and identical other args) to poll once.
Useful when fanning out many extractions in parallel without holding many
synchronous workers.

## Resuming a job by id

If a previous `veedcrawl_extract` / `veedcrawl_transcript` call already
produced a `job_id`, you can fetch the result later **without spending new
credits** (it is a plain `GET`). Three equivalent ways:

```jsonc
// dedicated lookup tool (recommended for clarity)
{"tool": "veedcrawl_job", "args": {"endpoint": "extract", "job_id": "abc123"}}

// or pass job_id alone to the original tool
{"tool": "veedcrawl_extract",    "args": {"job_id": "abc123"}}
{"tool": "veedcrawl_transcript", "args": {"job_id": "abc123"}}
```

`veedcrawl_metadata` is a sync `GET` and does **not** accept `job_id`.

## Limits & error codes

All errors return Hermes-standard `tool_error(...)` JSON. Notable codes:

| `code` | Meaning |
| --- | --- |
| `auth` | Missing / invalid API key |
| `insufficient_credits` | Balance below `cost × safety_factor` |
| `rate_limited` | Two consecutive 429s |
| `job_failed` | Async job ended with `status=failed` |
| `job_timeout` | Polling exceeded `timeout_s` (default 180 s) |
| `bad_request` | 4xx from the API (passed through) |

See <https://docs.veedcrawl.com> for the full API reference.
