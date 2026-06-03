---
name: kol-nox-contacts
description: Gate B Nox contacts before browser email-discovery.
tags: ["kol", "nox", "contacts", "email"]
---

# kol-nox-contacts

Gate **B** — fetch creator email via Nox when CAL has no `primary_email`.

## When to Use

- Console `POST /kols/{id}/nox-contacts` or post-approve contacts batch
- Config file must carry `nox_console_dispatch` with `pre_outreach_confirm`
- Do not invoke from `kol-reply-dispatcher` or Launch runs

## Prerequisites

- LIVE: `nox_kol_tool.py doctor --env LIVE` → `ok: true`
- `--campaign-config-file` only on `nox_kol_tool.py`, never on `kol_bridge_tool.py`

## Procedure

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py contacts \
  --env <env> \
  --campaign-config-file <path> \
  --gate pre_outreach_confirm \
  --nox-creator-id '<id>' \
  --audit-campaign-id <cid> --audit-identity-id <id>
```

On hit: `upsert-identity` + `identity.email_source=noxinfluencer_api`.

## Pitfalls

- **Failure**: `NOX_INSUFFICIENT_CREDIT` — stop; do not write cache.
- **Failure**: `NOX_AUTH_MISSING` — escalate; run `doctor --env LIVE`.
- **Success**: `cache_hit` same month — zero API.
