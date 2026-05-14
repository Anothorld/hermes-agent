# Veedcrawl Plugin

Native [Veedcrawl](https://veedcrawl.com) integration for Hermes. Veedcrawl is a
video-intelligence REST API that turns public YouTube / TikTok / Instagram /
X / Facebook URLs into metadata, transcripts, and structured AI extractions.

This plugin exposes **5 agent tools** that mirror Veedcrawl's official MCP
surface. Async jobs (`/v1/transcript`, `/v1/extract`) are polled inside the
client so agents see synchronous semantics.

## Tools

| Tool | Endpoint(s) | Cost | Notes |
| --- | --- | --- | --- |
| `veedcrawl_account` | `/v1/me`, `/health` | 0 | Probe + remaining credits |
| `veedcrawl_metadata` | `GET /v1/metadata` | 0 | Free first-pass video facts |
| `veedcrawl_transcript` | `POST /v1/transcript` (+ poll) | 1 native / 5 whisper | `mode`: `native` \| `generate` \| `auto` |
| `veedcrawl_extract` | `POST /v1/extract` (+ poll) | 10 | Custom prompt, optional JSON Schema |
| `veedcrawl_profile` | `/v1/{instagram,tiktok}/profile` | 0 | `platform`: `instagram` \| `tiktok` |

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
- **Response cache** — `metadata` / `profile` responses and completed
  `transcript` / `extract` jobs are cached at
  `~/.hermes/cache/veedcrawl/<endpoint>/<sha256>.json`. TTLs:

  | Endpoint | TTL |
  | --- | --- |
  | `metadata` | 24 h |
  | `profile` | 6 h |
  | `transcript`, `extract` | permanent (idempotent) |

  Pass `force_refresh: true` on a tool call to bypass the cache.

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
