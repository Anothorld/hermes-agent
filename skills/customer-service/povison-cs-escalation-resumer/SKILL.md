---
name: povison-cs-escalation-resumer
description: Resume Feishu-answered escalations into QuickCEP drafts.
trigger: Gateway brief contains escalation_resume after operator reply.
tags: [povison, customer-service, escalation, resume]
---

# Povison CS Escalation Resumer

After an operator replies in Feishu thread, incorporate their answer and write QuickCEP draft.

## Procedure

1. `get-escalation --env LIVE --escalation-id <id>`
2. `get-dispatch-context --session-id <quickcep_session_id>`
3. `quickcep_cli.py messages <id> --plain --chronological`
4. Merge `operator_answer` into customer reply (English, Povison tone)
5. `draft-save` with subject/body/receiver
6. `add-note` with escalation summary
7. `write-event --event-type escalation_resumed`
8. `update-session-status --status draft_ready`

## Pitfalls

**Ignoring operator_answer** — The Feishu reply is authoritative for policy exceptions.

**send-email** — Never auto-send; draft only.

## Examples

**Success:** Operator confirms assembly needs two people → draft reflects that → draft_ready.

**Failure:** Agent replies in Feishu instead of QuickCEP draft — wrong channel.
