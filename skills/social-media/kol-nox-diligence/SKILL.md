---
name: kol-nox-diligence
description: Nox API shortlist diligence with monthly cache.
tags: ["kol", "nox", "diligence", "shortlist"]
---

# kol-nox-diligence

Gate **A** — run only when the Console operator confirms shortlist
diligence for specific `identity_id`(s). Uses `nox_kol_tool.py` (not raw
`noxinfluencer`). Never sends mail.

## When to Use

- Console `POST /kols/{id}/nox-diligence` (or batch `identity_ids[]`).
- **Not** Launch discovery, **not** inbound `kol-reply-dispatcher`.

## Prerequisites

- `@noxinfluencer/cli` on PATH (`npm install -g @noxinfluencer/cli`).
- LIVE auth: `NOXINFLUENCER_API_KEY` in `$HERMES_HOME/.env` (Console brief
  prints `campaign_config_file` under the active profile). The bridge auto-runs
  `noxinfluencer auth --key-stdin` on first LIVE call when the env key is set.
- Preflight: `nox_kol_tool.py doctor --env LIVE` must report `ok: true`.
- `--env TEST` uses fixtures only (no real API).
- `campaign_config.nox_quota_enabled` must be true (Console + CLI enforce).

### Which CLI takes which flags

| Tool | `--campaign-config-file` | `--env` |
|------|--------------------------|---------|
| `nox_kol_tool.py` (gated subcommands on LIVE) | **Required** | Required |
| `kol_bridge_tool.py` (get-identity, get-facts, write-facts-multi) | **Never** | Required on mutating calls |

LIVE `--campaign-config-file` path comes from the Console gateway brief
(`campaign_config_file:`). Config must include signed `nox_console_dispatch`.
Do **not** hand-write that JSON.

Do **not** start a Nox gateway run from a free-form chat session — only Console briefs.

## Procedure

### 0 — Auth preflight (LIVE only; mandatory)

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py doctor --env LIVE
```

If `ok: false` or exit code `6`, open escalation `nox_auth_missing` and stop.

### 1 — Quota check (mandatory)

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py quota-snapshot --env <env>
```

Stop and escalate when:

- `error_code` is `NOX_AUTH_MISSING` or `remote_quota.error_code` is `NOX_AUTH_MISSING`
- `local.remaining_estimate` is 0 → escalation `nox_quota_exhausted`

### 2 — Resolve creator handle

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-identity \
  --identity-id <id> --env <env>
```

Do **not** pass `--campaign-config-file` to `kol_bridge_tool.py`.

Collect `platform`, profile URLs from facts, or `payload_json.nox_creator_id`.

Optional: `get-facts` — if `identity.nox_diligence_verdict` and
`identity.nox_cache_month` already exist for this month, report them and skip
Step 3 unless operator asked to refresh.

### 3 — Diligence pack (single CLI call)

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py diligence-pack \
  --env <env> \
  --campaign-config-file <path> \
  --gate shortlist_confirm \
  --nox-creator-id '<id>' \
  --platform <youtube|tiktok|instagram> \
  --url '<channel_url>' \
  --dimensions profile,audience,content \
  --lang en \
  --audit-campaign-id <cid> --audit-identity-id <id>
```

Batch (Console): `POST /kols/nox-diligence-batch` with `identity_ids[]`.

If only URL/handle is known, omit `--nox-creator-id` and pass `--platform` + `--url`.

**Cache is built in** — there is no `cache-lookup` subcommand. Re-run
`diligence-pack`; when `cache_hit: true`, report `api_calls: 0` and stop.

TikTok/Instagram: do **not** add `--include-cooperation` unless operator asked.

### 4 — Persist CAL facts

From `normalized_summary`, write via bridge:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <id> --campaign-id <cid> --env <env> --json @/tmp/nox_facts.json
```

Include at minimum:

- `identity.nox_creator_id`
- `identity.nox_diligence_verdict` (four-level string from summary)
- `identity.nox_diligence_at` (ISO8601 UTC)
- `identity.nox_cache_month` (from tool output)

### 5 — Report

Return: verdict, `cache_hit`, `cache_month`, `api_calls`, remaining quota hint.

## Pitfalls

- **Success**: `cache_hit` on second run same month — zero API cost.
- **Failure**: `NOX_AUTH_MISSING` — key missing or CLI not installed; escalate, do not retry in loop.
- **Failure**: `NOX_QUOTA_EXCEEDED` — escalate, do not retry in loop.
- **Failure**: `NOX_CAMPAIGN_GATE` / missing `nox_console_dispatch` — not Console-triggered.
- **Failure**: Inventing subcommands (`cache-lookup`) — forbidden; use `diligence-pack` only.
- **Failure**: Passing `--campaign-config-file` to `kol_bridge_tool.py` — wrong tool.

## Verification

`get-facts` shows `identity.nox_diligence_verdict` and `identity.nox_cache_month`.
