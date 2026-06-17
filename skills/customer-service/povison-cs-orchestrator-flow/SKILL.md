---
name: povison-cs-orchestrator-flow
description: Inbound QuickCEP email workflow for Povison CS automation.
trigger: Gateway brief contains cs_inbound_process or new QuickCEP email enqueue.
tags: [povison, customer-service, quickcep, orchestrator]
---

# Povison CS Orchestrator Flow

Processes one inbound QuickCEP email session: classify, auto-handle or escalate, write draft, apply lifecycle tags and internal notes.

## When to Use

- Watcher enqueued a session (`# cs_inbound_process` in gateway brief)
- Manual re-run for a stuck QuickCEP session

## Prerequisites

- `cs-ops-bridge` running (`serve.py` on port 8081)
- `povison-cs` gateway with API server (port 8643)
- `quickcep_cli.py` credentials in profile `.env`
- QuickCEP **AI客服** tags created (see bridge `config/session_tag_map.yaml`)

## Procedure

1. `skill_view(name='povison-cs-orchestrator-flow')` — this skill
2. `skill_view(name='quickcep')` — API commands
3. Bridge CLI (absolute path from brief `# bridge_cli_checklist`):
   - `get-dispatch-context --env LIVE --session-id <id>`
4. `quickcep_cli.py messages <id> --plain --chronological`
5. `classify-intent` via bridge CLI with email subject + body
6. **`apply-handoff --phase processing`** — tags (AI-处理中 + inquiry) + start note
7. **If route=auto_handle:**
   - Product → `povison-product-lookup` skill
   - Logistics → `povison-order-track` skill
   - Compose English reply → `draft-save` (never `send-email`)
   - **`apply-handoff --phase draft_ready`** — AI-草稿待审 + 待客户回复 + completion note
   - `update-session-status --status draft_ready`
8. **If route=escalate or review→escalate:**
   - `skill_view(name='povison-feishu-escalation')`
   - `send_message(action=send, target=feishu:AI客服后援, message=...)`
   - `open-escalation` with feishu thread/message ids from send result
   - **`apply-handoff --phase awaiting_expert`** — Escalation tag + escalation note
   - `update-session-status --status awaiting_expert`
9. **On unrecoverable error:** `apply-handoff --phase failed` then `update-session-status --status failed`

## apply-handoff fields

| Flag | Purpose |
|------|---------|
| `--customer-need` | 1–3 sentences: what the customer wants |
| `--actions-taken` | What you did (lookup, draft-save, escalate) |
| `--follow-up` | Next step for operators |
| `--operator-hint` | One-line handoff summary |
| `--classify-json` | JSON from classify-intent |
| `--feishu-thread-id` | Required for awaiting_expert phase |

Bridge applies QuickCEP tags and `add-note` automatically — **do not** call `tags-add` / `add-note` directly.

## Pitfalls

**Skipping apply-handoff** — Operators lose tags and internal notes; always call at processing start and branch end.

**Simulating Feishu send** — Must call `send_message` and verify `success` + `message_id`.

**send-email to customer** — Forbidden. Use `draft-save` only.

**Direct QuickCEP tag IDs** — Never hardcode; bridge owns tag map.

## Examples

**Success (product inquiry):** classify → processing handoff → product lookup → draft-save → draft_ready handoff → status draft_ready.

**Failure (skipped send_message):** escalation marked in chat only; operators never see request — always execute send_message.

**Failure (skipped apply-handoff):** draft saved but no AI-草稿待审 tag — operators cannot find pending drafts.
