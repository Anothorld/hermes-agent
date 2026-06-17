# CS Ops Bridge

State layer and watchers for Povison AI customer service (`povison-cs` profile).

## Quick start

```bash
# Terminal 1 — bridge + watchers
export FEISHU_APP_ID=...
export FEISHU_APP_SECRET=...
export QUICKCEP_EMAIL=...
export QUICKCEP_PASSWORD=...
export CS_OPS_GATEWAY_BASE=http://127.0.0.1:8643
python plugins/cs-ops-bridge/serve.py --port 8081

# Terminal 2 — povison-cs gateway (enable API_SERVER in profile config)
hermes -p povison-cs gateway run
```

## Profile config (`~/.hermes/profiles/povison-cs/config.yaml`)

```yaml
API_SERVER_ENABLED: true
API_SERVER_PORT: 8643
```

## Env vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `HERMES_CS_OPS_BRIDGE_KEY` | — | Mutations auth |
| `CS_OPS_BRIDGE_BASE` | `http://127.0.0.1:8081/api/plugins/cs-ops-bridge` | CLI target |
| `CS_OPS_GATEWAY_BASE` | `http://127.0.0.1:8643` | Agent launch |
| `CS_OPS_QUICKCEP_SKILL_DIR` | `~/.hermes/profiles/povison-cs/skills/social-media/quickcep` | CLI/SIO scripts |
| `CS_OPS_ENV` | `LIVE` | TEST/LIVE partition |
| `CS_OPS_GATEWAY_DRAIN` | `true` | Best-effort SSE drain after launch |
| `CS_OPS_ESCALATION_TIMEOUT_AUTO_START` | `true` | SLA reminder worker |
| `CS_OPS_ESCALATION_TIMEOUT_INTERVAL_SEC` | `900` | SLA check interval |
| `CS_OPS_ESCALATION_TIMEOUT_HIGH_H` | `2` | High urgency SLA (hours) |
| `CS_OPS_ESCALATION_TIMEOUT_MED_H` | `8` | Medium urgency SLA |
| `CS_OPS_ESCALATION_TIMEOUT_LOW_H` | `24` | Low urgency SLA |

## Phase 2 safeguards

- **Busy session guard**: new inbound while `processing` / `awaiting_expert` records `customer_followup_while_busy` and skips gateway launch.
- **Gateway launch**: retry on 429/502/503/504, in-process dedup, optional SSE drain (`gateway_launch.py`).
- **Escalation SLA**: `escalation_timeout.py` posts Feishu thread reminders and CAL events.
- **Agent guard**: enable plugin `cs-bridge-agent-guard` on the povison-cs gateway profile to block `send-email`.

## PII

Facts and event payloads are sanitized on write (`pii_sanitize.py`): emails, phones, card-like numbers, and street addresses are masked before SQLite persistence.

| `CS_OPS_HANDOFF_UNTRACKED_SENDS` | `false` | Apply handoff on operator send for untracked sessions |
| `CS_OPS_HANDOFF_SKIP_QUICKCEP` | — | Skip QuickCEP tag/note writes (CAL events only) |

## Session handoff (tags + internal notes)

Deterministic lifecycle labeling via `session_handoff.py`:

```bash
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py apply-handoff \
  --env LIVE --session-id <quickcep_session_id> \
  --phase draft_ready \
  --customer-need "Customer wants tracking" \
  --actions-taken "draft-save" \
  --operator-hint "Draft ready for review"
```

Phases: `processing`, `draft_ready`, `awaiting_expert`, `failed`, `reviewed`, `followup_while_busy`, `operator_sent`.

**Operator send:** SIO `operatorSendMsg` → automatic `operator_sent` handoff (tags + post-send note).

**Tag map:** `config/session_tag_map.yaml` — run `scripts/sync_session_tags.py` after creating **AI客服** tags in QuickCEP admin (`AI-处理中`, `AI-草稿待审`, `AI-待专家`, `AI-处理失败`, `AI-已结案`).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/sessions/{id}/handoff` | key | Apply lifecycle tags + internal note |

## Console API (Phase 3)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/sessions` | — | List/search sessions |
| POST | `/escalations/{id}/resume` | key | Launch gateway + resolve |
| POST | `/sessions/{id}/relaunch` | key | Retry failed/stuck session |

Operator UI: `playground/povison-cs-console/` (port 8092).

## CLI

```bash
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py health
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py get-dispatch-context --env LIVE --session-id <id>
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py classify-intent --env LIVE --subject "..." --body "..."
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py apply-handoff --env LIVE --session-id <id> --phase draft_ready --operator-hint "..."
python plugins/cs-ops-bridge/scripts/sync_session_tags.py   # after QuickCEP AI tags created
```

## Architecture

See [docs/povison-cs-architecture.md](../../docs/povison-cs-architecture.md).

## Skills

- `povison-cs-orchestrator-flow` — inbound processing
- `povison-cs-escalation-resumer` — post-Feishu resume
- `povison-feishu-escalation` — escalation message templates
