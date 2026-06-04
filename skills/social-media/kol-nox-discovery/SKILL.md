---
name: kol-nox-discovery
description: Manual Nox supplement search; no auto-ingest.
tags: ["kol", "nox", "discovery", "supplement"]
---

# kol-nox-discovery

**Supplement search only** — not Launch Step 3. Operator must enable
`campaign_config.nox_supplement_enabled`.

## When to Use

- Console `POST /campaigns/{id}/nox-supplement`.
- Brief states YouTube/TikTok pool gap after Instagram floor met.

## Prerequisites

- `nox_supplement_enabled: true` on campaign.
- Console `nox-supplement` button only (signed `nox_console_dispatch` in config file).
- LIVE: `nox_kol_tool.py doctor --env LIVE` → `ok: true`
- Quota remaining (`quota-snapshot`); stop on `NOX_AUTH_MISSING`.
- See `references/quota-budget.md` and `references/nox-to-ingest-mapping.md`.

## Procedure

1. `quota-snapshot --env <env>` — abort if exhausted.
2. Per platform (separate searches):

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py creator-search \
  --env <env> \
  --campaign-config-file <path> \
  --gate supplement_search \
  --platform youtube \
  --json '{"keywords":["..."],"page_num":1,"page_size":5}' \
  --audit-campaign-id <cid> --audit-identity-id 0
```

Max **one** `creator-search` per platform per gateway run.

3. Present `response.data.items` to operator — **do not** `ingest-confirmed-candidate` automatically.
4. After operator selects rows, one terminal call per handle:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id <cid> --env <env> --json @/tmp/ingest_<handle>.json
```

JSON must include top-level `source`, `identity`, and `candidate` (nested shape —
not flat `handle` / `profile_url`). See `references/nox-to-ingest-mapping.md`.

## Pitfalls

- **Failure**: Running during Launch — burns quota against floor policy.
- **Failure**: Auto-ingest without operator pick — wrong pool hygiene.

## Verification

Report `api_calls`, `cache_hit`, monthly `cache-stats`.
