# Veedcrawl REST API (discovery)

Canonical reference for the Hermes **plugin** (`veedcrawl_*` tools). Production
discovery uses plugin tools with kol-ops-bridge monthly persist — not MCP.

Official docs: https://docs.veedcrawl.com/api-reference/search

## Authentication

Header: `x-api-key: $VEEDCRAWL_API_KEY` (not `Authorization: Bearer`).

## Endpoints used in KOL discovery

| Endpoint | Method | Credits | Notes |
|---|---|---|---|
| `/v1/me` | GET | 0 | Account + `creditsRemaining` |
| `/v1/search` | POST (+ poll `GET /v1/search/{jobId}`) | ~5–15 / platform | JSON body: `query`, `platforms[]`, `limit`≤**20**; returns `result.results[]` |
| `/v1/instagram/profile` | GET | 0 | `username` or `url`, `limit`≤**24**; `stats.followers`, `videos[]` |
| `/v1/metadata` | GET | 0 | `?url=`; flat `viewCount` / `likeCount` or nested `stats` |
| `/v1/extract` | POST + poll | 10 | JSON body `{url,prompt,schema?,lang?}`; poll `GET /v1/extract/{jobId}` |
| `/v1/transcript` | POST + poll | 1–5 | **`url` in query string**, not JSON body |

## Transcript pitfall

```bash
# Correct
curl -X POST "https://api.veedcrawl.com/v1/transcript?url=https%3A%2F%2F...&mode=auto" \
  -H "x-api-key: $VEEDCRAWL_API_KEY"

# Wrong — url in JSON body fails validation
```

## Search (async)

```bash
# Submit
curl -X POST "https://api.veedcrawl.com/v1/search" \
  -H "x-api-key: $VEEDCRAWL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"cozy home decor","platforms":["instagram"],"limit":12}'

# Poll until status=completed, then read result.results[]
curl "https://api.veedcrawl.com/v1/search/{jobId}" \
  -H "x-api-key: $VEEDCRAWL_API_KEY"
```

Agent tools pass `q` / `platform` / `limit`; the plugin maps them to `query` /
`platforms` / `limit` on submit.

## Plugin vs MCP

| | REST plugin | MCP |
|---|---|---|
| Search | `POST /v1/search` async job (~5–15 credits / platform) | Same REST contract via MCP |
| Discovery | **Use plugin** | IDE debugging only |

## Monthly persist (kol-ops-bridge)

- Path: `$HERMES_HOME/kol-ops-bridge/veedcrawl_cache/`
- Month: Asia/Shanghai `YYYY-MM`
- Envelope fields: `cache_hit`, `persisted`, `storage_ref`, `response`
- `storage_ref`: blob file path or `sqlite:{month}:{cache_key}`
- CAL index writes `identity.veedcrawl_storage_ref` (legacy alias `identity.veedcrawl_blob_ref`)
- `extract` with `wait=false` sets `persisted: false` until the job completes

See also: `veedcrawl-tools.md` (tool names, budgets, fallback rules).
