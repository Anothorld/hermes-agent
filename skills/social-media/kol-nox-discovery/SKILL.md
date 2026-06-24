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

## Hard stop on quota / auth failure (read first)

Nox is the **only** sanctioned discovery surface for this supplement. When Nox
cannot serve a request, **STOP the run** — do **not** substitute manual scraping.

Stop immediately (do not retry, do not fall back) when any of these appear:

- `quota-snapshot` shows remaining ≤ 0, or `creator-search` returns
  `SaaS 40017` / `配额不足` (quota exhausted).
- `nox_kol_tool.py doctor` ≠ `ok: true`, or any `NOX_AUTH_MISSING`.

On a hard stop, end the run with a one-line report and the diagnostic field
`floor_unmet_reason: nox_quota_exhausted` (or `nox_auth_missing`). The operator
recharges quota / fixes auth, then re-fires the supplement. Burning 80+ agent
iterations on browser fallbacks is the documented SSF8033 failure mode — a
clean stop is the correct outcome, not a partial workaround.

**Forbidden fallbacks** (these caused the 90-iteration burn):

- ❌ `browser_*` / TikTok / YouTube manual navigation+snapshot to "find creators
  by hand" when Nox is unavailable. Nox supplement has no browser path.
- ❌ Re-issuing the same `creator-search` after a quota/auth error hoping it
  clears. It will not within the run.
- ❌ Retrying the same `ingest-confirmed-candidate` more than **twice** after a
  validation (400/422) error — fix the payload shape per the mapping reference
  or move the handle to `pending_ingests` and continue.

## Procedure

1. `quota-snapshot --env <env>` — abort if exhausted (hard stop above).
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
- **Failure (SSF8033)**: Quota hit `配额不足` at 22:12 → agent fell back to
  TikTok/Nox **browser automation** and looped to `max_iterations_reached(90/90)`.
  Correct response is the **hard stop** above (`floor_unmet_reason:
  nox_quota_exhausted`), never a manual browser substitute.
- **Failure (SSF8033)**: Repeated `ingest-confirmed-candidate` 400/422 from
  missing provenance triples / disallowed fields / wrong nested shape, retried
  many times. Fix the payload once per `references/nox-to-ingest-mapping.md`,
  retry **at most twice**, then defer to `pending_ingests`.

## Provenance & ingest shape (avoid 400/422 loops)

`ingest-confirmed-candidate` JSON must use the **nested** `source` / `identity`
/ `candidate` shape (not flat `handle`/`profile_url`). Every `identity.*_url`
or descriptive fact carries its provenance triple in the **same** write:
`<field>_source`, `<field>_discovered_at`, `<field>_discovered_url`. See
`references/nox-to-ingest-mapping.md` for the exact field map. A 409
`discovery_skip_active` means the handle is a prior-collab — skip it, do not
re-ingest.

## Verification

Report `api_calls`, `cache_hit`, monthly `cache-stats`.
