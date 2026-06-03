---
name: kol-nox-monitor
description: One-shot Nox video monitor setup after publish confirm.
tags: ["kol", "nox", "monitor", "post-publish"]
---

# kol-nox-monitor

Gate **C** — register a published video URL in Nox monitor **once**.
No cron `monitor history` polling (quota).

## When to Use

- Console `POST /kols/{id}/nox-monitor` with confirmed `video_url`.
- LIVE: `--campaign-config-file` on `nox_kol_tool.py` only (Console-signed `nox_console_dispatch`).

## Prerequisites

- LIVE: `nox_kol_tool.py doctor --env LIVE` → `ok: true`

## Procedure

1. Dry-run preview:

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py monitor-setup \
  --env <env> \
  --campaign-config-file <path> \
  --gate post_publish_confirm \
  --video-url '<url>'
```

2. After operator confirms, re-run with `--force`.

3. Persist facts:

- `identity.nox_monitor_project_id`
- `identity.nox_monitor_task_id`
- from `response` payload

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <id> --campaign-id <cid> --env <env> --json @/tmp/monitor_facts.json
```

4. `write-event` type `nox_monitor_registered` (optional).

## Pitfalls

- **Failure**: Scheduling cron history pulls — forbidden.
- **Success**: `cache_hit` same month same URL — zero API calls.

## Verification

Facts present; operator uses Nox dashboard for ongoing metrics.
