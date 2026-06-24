---
name: povison-cs-escalation-resumer
description: Resume Feishu-answered escalations into QuickCEP drafts.
trigger: Gateway brief contains escalation_resume after operator reply.
tags: [povison, customer-service, escalation, resume]
---

# Povison CS Escalation Resumer

After an operator replies in Feishu thread, incorporate their answer and write QuickCEP draft with lifecycle handoff.

## Procedure

1. `get-escalation --env LIVE --escalation-id <id>`
2. `get-dispatch-context --session-id <quickcep_session_id>`
3. `get-messages --env LIVE --session-id <id>`
4. Merge `operator_answer` into customer reply (English, Povison tone)
5. **`cs_bridge_tool draft-save`** (auto join-chat) with subject/body/receiver — use `--content-file` when draft contains `$`
6. **`apply-handoff --phase draft_ready`** — tags + internal note (expert answer summary)
7. `write-event --event-type escalation_resumed`
8. `update-session-status --status draft_ready`

## apply-handoff example

```bash
python3 cs_bridge_tool.py apply-handoff --env LIVE --session-id <id> \
  --phase draft_ready \
  --customer-need "<中文：客户原始诉求>" \
  --actions-taken "已合并飞书专家答复并保存草稿" \
  --operator-hint "<中文：下一操作员接手要点>"
```

**备注语言：** 所有 apply-handoff 文本字段必须使用简体中文（客户邮件草稿除外）。

## Pitfalls

**Ignoring operator_answer** — The Feishu reply is authoritative for policy exceptions.

**send-email** — Never auto-send; draft only.

**Skipping apply-handoff** — No AI-草稿待审 tag or handoff note after resume.

## Examples

**Success:** Operator confirms assembly needs two people → draft reflects that → apply-handoff draft_ready → draft_ready status.

**Failure:** Agent replies in Feishu instead of QuickCEP draft — wrong channel.
