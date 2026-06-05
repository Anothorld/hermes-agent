# Nox audience screen (discovery)

Use during **per-candidate qualification**, after a profile visit passes handle /
follower pre-checks and **before** deep Reel scoring (views, ER, 5-Reel activity).

## When enabled

Brief must include **both**:

- `nox_discovery_enabled: true`
- `campaign_config_file: <path>` (Console-issued; LIVE only)

If either is missing, skip Nox and use browser-only region signals.

Also require `campaign_config.nox_quota_enabled: true` in the materialized JSON.

## Run-start preflight (once per discovery run)

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py doctor --env LIVE
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py quota-snapshot --env LIVE
```

Abort Nox for the rest of the run on `NOX_AUTH_MISSING` or `local.remaining_estimate == 0`
(open escalation `nox_quota_exhausted`). Browser discovery may continue.

## Per-candidate flow

1. `browser_navigate` to `https://www.instagram.com/<handle>/` — confirm handle,
   followers ≥ 100k, obvious brand/agency discard.
2. **Cache check (CAL)** — if identity exists:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-facts \
  --identity-id <id> --env <env>
```

When `identity.nox_cache_month` is the current month (Asia/Shanghai, same as Nox cache)
and `identity.nox_top_region` is present, reuse facts — **do not** call Nox again.

3. **Nox audience pack** (when CAL cache miss):

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py diligence-pack \
  --env <env> \
  --campaign-config-file <path> \
  --gate discovery_qualify \
  --platform instagram \
  --url 'https://www.instagram.com/<handle>/' \
  --dimensions audience \
  --lang en \
  --audit-campaign-id <cid> --audit-identity-id 0
```

`cache_hit: true` → `api_calls: 0` (monthly SQLite cache; safe to re-run).

4. **Persist immediately** (even on discard):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-identity \
  --env <env> --json '{"primary_handle":"<handle>","platform":"instagram"}'
```

Map `normalized_summary` to facts (minimum keys from audience pack):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <id> --env <env> --json @/tmp/nox_audience_facts.json
```

Use `campaign_id=null`. Include at least:
`identity.nox_creator_id`, `identity.nox_cache_month`, `identity.nox_top_region`,
`identity.nox_audience_authenticity`, `identity.nox_gender_skew`,
`identity.nox_audience_age_distribution`, `identity.nox_audience_languages_top`,
`identity.nox_diligence_at`, `identity.nox_api_calls_last`, `identity.nox_cache_hit`.

Prefer mapping via `plugins/nox-kol-bridge/internal/diligence_facts.py`
(`identity_facts_from_diligence`) when running a small helper script is impractical.

5. **Screen rules** (discard before Reel deep dive):

| Signal | Discard when |
|--------|----------------|
| Top audience regions | No US/Canada in top-3 **or** combined US+CA share clearly &lt; 40% |
| Audience authenticity | Nox reports very low authenticity / heavy fake-audience flags |
| Quota / auth | `NOX_QUOTA_EXCEEDED` / `NOX_AUTH_MISSING` — skip Nox, do not discard solely on API miss |

Log one line: `nox_audience_discard: @handle — <reason>`.

6. Survivors continue Reel qualification; include the same `identity_facts` in
   `ingest-confirmed-candidate` payload.

## Quota notes

- Discovery uses **1 API credit** per handle per month (`audience` dimension only).
- Gate A shortlist diligence reuses cached `audience` and fetches only missing
  dimensions (`profile`, `content`, `cooperation`) — no duplicate audience billing.

## Forbidden

- `--gate shortlist_confirm` or `--dimensions profile,audience,content,cooperation` during discovery.
- `kol-nox-discovery` / `creator-search` during Launch Step 3.
- Passing `--campaign-config-file` to `kol_bridge_tool.py`.
