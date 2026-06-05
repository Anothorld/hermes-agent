# nox-kol-bridge

Deterministic wrapper for [`@noxinfluencer/cli`](https://www.npmjs.com/package/@noxinfluencer/cli) with:

- **Monthly quota ledger** (default budget 1800 calls/month)
- **Monthly response cache** (same query in `YYYY-MM` never hits API twice)
- **Alias index** (URL/handle → `nox_creator_id`)
- **Auto-auth** from `NOXINFLUENCER_API_KEY` in Hermes `.env` on first LIVE call

## Install

```bash
npm install -g @noxinfluencer/cli@latest
```

Add the API key to your Hermes profile (not committed):

```bash
# ~/.hermes/profiles/kol-orchestrator/.env  (or $HERMES_HOME/.env)
NOXINFLUENCER_API_KEY=your-nox-skills-api-key
```

Register / obtain a key: https://www.noxinfluencer.com/skills

Preflight (auto-runs `noxinfluencer auth --key-stdin` when the env key is set):

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py doctor --env LIVE
```

Manual auth (optional if env key + doctor bootstrap works):

```bash
printf '%s\n' "$NOXINFLUENCER_API_KEY" | noxinfluencer auth --key-stdin
```

### Why “API key in .env” is not enough alone

`@noxinfluencer/cli` reads **`~/.noxinfluencer/config.json`**, not
`NOXINFLUENCER_API_KEY` at runtime. Hermes loads the env var from
`$HERMES_HOME/.env`, but the Nox CLI still needs a one-time persist via
`auth`. This bridge hydrates the env var from profile `.env` files and
calls `auth --key-stdin` automatically before LIVE subprocess calls.

## Usage

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py doctor --env LIVE
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py quota-snapshot --env LIVE

python plugins/nox-kol-bridge/scripts/nox_kol_tool.py diligence-pack \
  --env LIVE --gate shortlist_confirm \
  --campaign-config-file ~/.hermes/kol-ops/nox_campaign_configs/<campaign_id>.json \
  --nox-creator-id '<id>' --lang en

python plugins/nox-kol-bridge/scripts/nox_kol_tool.py contacts \
  --env LIVE --gate pre_outreach_confirm --nox-creator-id '<id>'

python plugins/nox-kol-bridge/scripts/nox_kol_tool.py cache-stats
```

`--env TEST` uses fixtures under `tests/fixtures/` and never calls Nox.

**Do not** pass `--campaign-config-file` to `kol_bridge_tool.py` — that flag
is only for gated `nox_kol_tool.py` subcommands on LIVE.

## Subcommands

| Subcommand | Auth on LIVE | `--campaign-config-file` |
|------------|--------------|---------------------------|
| `doctor` | checks / bootstraps | no |
| `quota-snapshot` | yes | optional |
| `diligence-pack` | yes | required |
| `contacts` | yes | required |
| `creator-search` | yes | required |
| `monitor-setup` | yes | required |
| `cache-stats` | no | no |

There is **no** `cache-lookup` — cache hits are returned by re-running the
same gated subcommand (`cache_hit: true`, `api_calls: 0`).

**Console backend:** `cache-stats` must not receive `--env` (local ledger only).

## Gates

| Gate | Subcommand |
|------|------------|
| `shortlist_confirm` | `diligence-pack` (Gate A; incremental dim fetch) |
| `discovery_qualify` | `diligence-pack` (`audience` only during IG discover) |
| `pre_outreach_confirm` | `contacts` |
| `supplement_search` | `creator-search` |
| `post_publish_confirm` | `monitor-setup` |

## Cache location

`$HERMES_HOME/kol-ops-bridge/nox_cache/nox_cache.db` (mode `0600`).

## Skills

- `kol-nox-diligence` — Gate A
- `kol-nox-discovery` — supplement search (manual)
- `kol-nox-monitor` — Gate C

CAL writes still go through `kol-ops-bridge` / `kol_bridge_tool.py`.

**Console Gate A/B** call `nox_kol_tool.py` synchronously and hydrate facts via
`internal/diligence_facts.py` (`identity_facts_from_diligence` /
`identity_facts_from_contacts`) — operators see categorized Nox metrics on the
KOL detail dashboard without relying on gateway agents to hand-write keys.

### Audience facts (`summarize.py` → `identity.nox_*`)

| CAL key | Nox `audience` source |
|---------|----------------------|
| `nox_top_region` | `regions[]` (`name` + `value` as %) |
| `nox_gender_skew` | `genders[]` or legacy `female_ratio` |
| `nox_audience_age_distribution` | `female_ages` / `male_ages` or `follower_ages` |
| `nox_audience_adults_split` | `adults[]` |
| `nox_audience_languages_top` | `languages[]` |
| `nox_audience_types_top` | `audience_types[]` |
| `nox_audience_authenticity` | `audience_authenticity` |
| `nox_audience_quality_score` | `audience_quality` |
| `nox_audience_positive_pct` | `positive_audience_pct` |
| `nox_audience_promo_*` | `promo_attractiveness`, `promo_interested_audience_pct`, `promo_professionalism` |
| `nox_audience_interests_top` | `audience` or **`content.audience_interests`** (`keyword`) |
| `nox_median_views`, `nox_wave`, `nox_avg_active_days`, `nox_view_per_followers` | `profile` performance |
| `nox_performance_levels`, `nox_benchmark_ranks` | `profile` `*_level` and `*_benchmark.rank` |
| `nox_content_format_counts`, `nox_content_engagement_split` | `profile` posts/reels/pics counts |
| `nox_content_tags_all` | `content.all_tags` |
| `nox_cooperation_*`, `nox_dispute_types` | `cooperation` dimension (Gate A default) or `profile` fallback |
| `nox_cooperation_price_*`, `nox_cooperation_brands_top`, etc. | `cooperation` detail: pricing, brands, response hours |

Gate A default dimensions: `profile,audience,content,cooperation` (**4** `api_calls`).

Re-run `diligence-pack` after upgrading the bridge to backfill keys for creators
diligenced before these fields were mapped.

## LIVE campaign gates

`diligence-pack`, `contacts`, `creator-search`, and `monitor-setup` require
`--campaign-config-file` with `nox_quota_enabled: true` on LIVE.
`creator-search` also requires `nox_supplement_enabled: true`.

## Console-only dispatch (P3)

LIVE gated commands also require `nox_console_dispatch` (HMAC) inside the
campaign config JSON. **KOL Ops Console** signs the claim when materializing
`~/.hermes/kol-ops/nox_campaign_configs/<campaign_id>.json`. Gateway workers need
`NOX_CONSOLE_DISPATCH_SECRET` or `HERMES_KOL_OPS_BRIDGE_KEY` matching Console.
Dev only: `NOX_SKIP_CONSOLE_DISPATCH=1`.

## CLI notes

Nox `creator_id` values may start with `-`. Commander cannot parse them as positional
args (and a bare `--` is consumed as the ID). The bridge falls back to `--url` /
`--platform --channel-id` when available, otherwise issues the same GET as the CLI
via `internal/creator_http.py`.

## Error codes (CLI exit)

| Code | Exit | Meaning |
|------|------|---------|
| `NOX_AUTH_MISSING` | 6 | CLI not authed; fix `doctor --env LIVE` |
| `NOX_CAMPAIGN_GATE` | 3 | Missing/disabled campaign config or dispatch |
| `NOX_QUOTA_EXCEEDED` | 2 | Local monthly budget exhausted |
| `NOX_CLI_ERROR` | 1 | Upstream `noxinfluencer` failure |

## API credits vs `api_calls`

Tool `api_calls` counts only the current subcommand. Nox dashboard totals include
`quota`, failed partial runs, and manual CLI probes — see `docs/kol-nox-integration.md`.

## Out of scope

Nox email/CRM/collection write APIs; Launch bulk discovery.
