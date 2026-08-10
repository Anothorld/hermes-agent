# serp-api

SERP API client for the SEO Studio brainstorm step. Provides the `serp_fetch_google`
tool — a **drop-in replacement** for the browser-based `rpa_fetch_google_serp` that
works on datacenter IPs (which get CAPTCHA'd by direct Google scraping).

## Why

The brainstorm step (`povison-seo-blog` skill, Step 2) does 8–10 Google SERP queries
to analyze top-10 competitors and find content gaps. On a self-hosted server the
egress IP is a datacenter IP, so Google/Bing/DDG/Mojeek **all return CAPTCHAs** — the
browser-based `rpa_fetch_google_serp` cannot return results. This plugin calls a
SERP API from a clean IP instead, with no browser/CDP dependency.

## Tool: `serp_fetch_google`

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | required, URL-encoded by the provider |
| `max_results` | int | 10 | max 40 (Google CSE caps at 10/request) |
| `gl` | string | `us` | Google geo |
| `hl` | string | `en` | Google language |

Returns the same shape as `rpa_fetch_google_serp` so the skill consumes it unchanged:

```json
{
  "ok": true,
  "data": {
    "query": "pet-friendly sofa small apartment",
    "results": [
      {"rank": 1, "title": "...", "url": "https://...", "snippet": "..."}
    ],
    "count": 10,
    "provider": "google_cse"
  },
  "errors": [],
  "meta": {"elapsed_ms": 420, "provider": "google_cse", "cached": false, "cache_ttl_s": 86400}
}
```

## Providers

Selected via `SERP_API_PROVIDER` (default `serper`).

| Provider | Env vars | Free tier | Card? | Notes |
|---|---|---|---|---|
| `serper` (default) | `SERPER_API_KEY` (or `SERP_API_KEY`) | **2,500 free credits** (one-time) | **No** | [serper.dev](https://serper.dev) — real Google results from a clean provider IP (no datacenter CAPTCHA). Best free option for self-hosted servers. ~250 brainstorms; $50/50k after. Up to 40 results/query. |
| `serpapi` | `SERPAPI_KEY` (or `SERP_API_KEY`) | 250/mo (recurring) | No | [serpapi.com](https://serpapi.com) — Google+Bing+others. Good for ongoing low volume. |
| `google_cse` | `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_CX` | 100 queries/day | No | [Programmable Search Engine](https://programmablesearchengine.google.com/) — "Search the entire web", enable Custom Search API + API key in Google Cloud. Max 10/query. May 403 if key has API restrictions. |
| `brave` | `BRAVE_SEARCH_API_KEY` (or `BRAVE_API_KEY` / `SERP_API_KEY`) | free tier removed late 2025; $5/mo credit only | **Yes** | [Brave Search API](https://brave.com/search/api/) — real web results, clean IP. Not recommended in 2026 (needs a card). count ≤ 20. |
| `valueserp` | `VALUESERP_KEY` (or `SERP_API_KEY`) | 100/mo | No | [valueserp.com](https://www.valueserp.com) — $1/1k results. |
| `wigolo` | `WIGOLO_API_URL` (default `http://127.0.0.1:3333`); `WIGOLO_API_TOKEN` only if bound off-loopback | **$0/query, no key, no cloud** | No | Self-hosted daemon (`wigolo serve`). ⚠️ No Google adapter; on datacenter IPs the non-bing engines get CAPTCHA'd and bing returns poor-locale results — verify quality before relying on it. |

A generic `SERP_API_KEY` is honored as a fallback for all paid providers. `wigolo` needs
no key — only the daemon URL (loopback is open by default).

### serper (recommended free option for self-hosted / datacenter IPs)

When the gateway's egress IP is flagged by search engines (CAPTCHAs on Google/Bing/DDG),
browser-based SERP, wigolo's direct adapters, and direct scraping all fail. **Serper**
returns real Google results via an API call from a clean provider IP — no browser, no
datacenter CAPTCHA. 2,500 free credits on signup (no credit card), enough for ~250
brainstorms; $50 / 50k after.

Setup:

1. Register at [serper.dev](https://serper.dev) (email only, no credit card) and create
   an API key.
2. Put the key in the deploy-safe env file (e.g. `/opt/povison-seo/data/serp.env`):
   ```
   SERP_API_PROVIDER=serper
   SERPER_API_KEY=<your-key>
   ```
3. Recreate the gateway. Done — `serp_fetch_google` now calls Serper.

## Other env vars

| Var | Default | Purpose |
|---|---|---|
| `SERP_API_ENABLED` | `1` | Master kill switch (`0` disables the tool). |
| `SERP_API_CACHE_TTL` | `86400` | Cache TTL in seconds (24h). `0` disables caching. |
| `SERP_API_CACHE_DIR` | `$HERMES_HOME/.cache/serp-api` | Cache dir (persists on the data volume). |
| `SERP_API_TIMEOUT` | `15` | HTTP timeout seconds. |
| `SERP_API_MAX_RETRIES` | `3` | Retries on 429/5xx (exponential backoff). |
| `SERP_API_BREAKER_THRESHOLD` | `5` | Consecutive failures before the circuit opens. |
| `SERP_API_BREAKER_RESET_S` | `60` | Seconds the circuit stays open. |

## Caching

Results are cached to `$HERMES_HOME/.cache/serp-api/<sha1>.json` keyed by
`(provider, query, gl, hl)`. The cache lives on the bind-mounted data volume, so it
survives container restarts and saves quota across brainstorm re-runs. Cached hits
return `meta.cached: true` and `elapsed_ms: 0`.

## Setup (free tier — Google Custom Search)

1. Create a Programmable Search Engine at
   <https://programmablesearchengine.google.com/> → "Search the entire web" → copy the
   **CX** (e.g. `1234abcd:efgh`).
2. Google Cloud Console → enable **Custom Search API** → create an **API key**.
3. Set env in the gateway (compose.yml):
   ```yaml
   environment:
     - SERP_API_PROVIDER=google_cse
     - GOOGLE_CSE_API_KEY=AIza...
     - GOOGLE_CSE_CX=1234abcd:efgh
   ```
4. Add `serp-api` to `plugins.enabled` in the povison-seo profile `config.yaml`.

## Switching to a paid provider later

Set `SERP_API_PROVIDER=serper` + `SERPER_API_KEY=...` (or use `SERP_API_KEY`). No code
or skill change needed — the tool auto-dispatches to the configured provider.

## Tests

```bash
cd hermes-agent/plugins/serp-api && python3 -m pytest tests/ -q
```

## Caveats

- Google CSE returns **up to 10** organic results per query (no PAA/ads/knowledge
  panel). For brainstorm gap analysis (top-10 competitors) this is sufficient; for
  deeper SERP feature analysis switch to `serper`/`serpapi`.
- CSE results are not identical to google.com SERP ordering (CSE uses a different
  index). Titles + URLs are still reliable for competitor/gap analysis.
- SV/KD/CPC metrics are **not** provided here — they stay on the existing
  `kw.json` / `enrich-keyword-metrics.py` (Semrush proxy) path.
