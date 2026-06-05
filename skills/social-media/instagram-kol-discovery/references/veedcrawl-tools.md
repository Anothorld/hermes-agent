# Veedcrawl plugin tools (discovery supplement)

Browser Instagram discovery remains primary. These Hermes **plugin** tools
(`veedcrawl` toolset) reduce page loads and cache API responses for the month.

**Not for production discovery:** `mcp_veedcrawl_*` (IDE debugging only).

## Tools

| Tool | REST | Cost | Persist |
|---|---|---|---|
| `veedcrawl_search_social_videos` | `GET /v1/search` | 0 | Yes |
| `veedcrawl_instagram_profile` | `GET /v1/instagram/profile` | 0 | Yes |
| `veedcrawl_metadata` | `GET /v1/metadata` | 0 | Yes |
| `veedcrawl_extract` | `POST /v1/extract` + poll | 10 | Yes |
| `veedcrawl_account` | `GET /v1/me` | 0 | No |
| `veedcrawl_profile` | IG/TikTok profile | 0 | Yes |
| `veedcrawl_transcript` | async transcript | 1–5 | Plugin TTL cache only |
| `veedcrawl_job` | job lookup | 0 | No |

## Common optional args (discovery tools)

- `env`: `TEST` | `LIVE` (default `LIVE`)
- `identity_id`: writes `identity.veedcrawl_*` CAL index facts when set
- `handle`: IG handle for fact attribution
- `force_refresh`: bypass monthly persist cache (still writes new blob)

## Response envelope

```json
{
  "ok": true,
  "operation": "get_instagram_profile",
  "cache_month": "2026-06",
  "cache_key": "profile:ig:kathypicos:limit=12",
  "cache_hit": false,
  "api_calls": 1,
  "persisted": true,
  "blob_ref": "/path/to/blob.json",
  "storage_ref": "sqlite:2026-06:profile:ig:kathypicos:limit=12",
  "identity_facts_written": true,
  "response": {}
}
```

Read metrics from `response`. Use `cache_hit` / `persisted` / `storage_ref` in diagnostics.
`storage_ref` is always set when `persisted: true` (blob path or `sqlite:{month}:{key}`).

REST details: `veedcrawl-api.md`.

## Per-run budgets (instagram-kol-discovery)

| Tool | Max per run |
|---|---|
| `veedcrawl_search_social_videos` | 3 |
| `veedcrawl_extract` | 10 |
| Browser profiles / reel loads | 40 / 200 (unchanged) |

## Monthly cache

- Storage: `$HERMES_HOME/kol-ops-bridge/veedcrawl_cache/` (SQLite + blobs)
- Month key: Asia/Shanghai `YYYY-MM`
- Search cache is **global** across campaigns (same `q` + `platform` + `limit`)
- Retention: 3 months default

## Fallback rules

1. `ok: false` or `persisted: false` → browser for that signal
2. Veedcrawl toolset disabled → pure browser (run continues)
3. Profile followers null from API → `browser_navigate` profile for counts
4. Bio / region / furniture self-sell → **browser only**

## CAL index facts (when `identity_id` set)

- `identity.veedcrawl_profile_followers`
- `identity.veedcrawl_recent_reels_stats` (list of `{url, views, likes}`)
- `identity.veedcrawl_cache_month`, `identity.veedcrawl_cache_key`, `identity.veedcrawl_storage_ref` (alias `identity.veedcrawl_blob_ref`)
- `identity.veedcrawl_extract_summary` (after extract)

Full JSON always lives in blob; facts are indexes only.
