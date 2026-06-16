---
name: povison-cs-orchestrator-flow
description: Inbound QuickCEP email workflow for Povison CS automation.
trigger: Gateway brief contains cs_inbound_process or new QuickCEP email enqueue.
tags: [povison, customer-service, quickcep, orchestrator]
---

# Povison CS Orchestrator Flow

Processes one inbound QuickCEP email session: classify, auto-handle or escalate, write draft.

## When to Use

- Watcher enqueued a session (`# cs_inbound_process` in gateway brief)
- Manual re-run for a stuck QuickCEP session

## Prerequisites

- `cs-ops-bridge` running (`serve.py` on port 8081)
- `povison-cs` gateway with API server (port 8643)
- `quickcep_cli.py` credentials in profile `.env`

## Procedure

1. `skill_view(name='povison-cs-orchestrator-flow')` — this skill
2. `skill_view(name='quickcep')` — API commands
3. Bridge CLI (absolute path from brief `# bridge_cli_checklist`):
   - `get-dispatch-context --env LIVE --session-id <id>`
4. `quickcep_cli.py messages <id> --plain --chronological`
5. `classify-intent` via bridge CLI with email subject + body
6. **If route=auto_handle:**
   - Product → `povison-product-lookup` skill
   - Logistics → `povison-order-track` skill
   - Compose English reply → `draft-save` (never `send-email`)
   - `write-event` + `update-session-status --status draft_ready`
7. **If route=escalate or review→escalate:**
   - `skill_view(name='povison-feishu-escalation')`
   - `send_message(action=send, target=feishu:AI客服后援, message=...)`
   - `open-escalation` with feishu thread/message ids from send result
   - `update-session-status --status awaiting_expert`

## Pitfalls

**Simulating Feishu send** — Must call `send_message` and verify `success` + `message_id`.

**send-email to customer** — Forbidden. Use `draft-save` only.

**Direct QuickCEP API** — Use `quickcep_cli.py` via terminal only.

## Examples

**Success (product inquiry):** classify → product lookup → draft-save → status draft_ready.

**Failure (skipped send_message):** escalation marked in chat only; operators never see request — always execute send_message.
