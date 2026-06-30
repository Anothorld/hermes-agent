---
name: povison-cs-escalation-resumer
description: Resume Feishu-answered escalations into QuickCEP drafts.
trigger: Gateway brief contains escalation_resume after operator reply.
tags: [povison, customer-service, escalation, resume]
---

# Povison CS Escalation Resumer

After an operator replies in Feishu thread, incorporate their answer and write QuickCEP draft with lifecycle handoff.

## Tool rules (mandatory)

- **`cs_bridge_tool` only** for bridge/QuickCEP operations
- **`terminal` tool only** — one `python3 <cs_bridge_tool> …` command per call
- **Never `execute_code` / subprocess.run** to batch bridge steps (blocked on `povison-cs:*` runs)
- **Never `quickcep_cli` directly**
- Use **quickcep_session_id** (long numeric id) — never CAL internal session id

## 飞书专家附件 SOP（必读）

**顺序：先点 ESC 消息里的上传链接完成附件上传，再在本主题回复文字。**

先回复、后上传的文件 **不会** 自动进入 QuickCEP 草稿。PDF 必须通过上传链接（Vault）；飞书直发 PDF 不支持。

## Procedure

0. `skill_view(name='povison-cs-escalation-resumer')` — this skill
1. **terminal:** `get-escalation --env LIVE --escalation-id <id>`
2. **terminal:** `get-dispatch-context --env LIVE --session-id <quickcep_session_id>`
3. **terminal:** `get-messages --env LIVE --session-id <quickcep_session_id>`
4. Merge `operator_answer` into customer reply (English, Povison tone) → `/tmp/draft-<quickcep_session_id>.html`
   - **Plain text is OK** — `draft-save` converts `\n\n` to `<p>` and single `\n` to `<br>`.
   - **Do not** wrap in `<html><body>` with raw newlines (QuickCEP ignores `\n` in HTML).
   - Prefer `<p>...</p>` only if you hand-write HTML; or plain text / minimal `<p>` blocks.
5. **terminal:** Hindsight `retain` (required before draft-save) — see orchestrator/resumer checklist
6. **terminal:** `draft-save --env LIVE --session-id <quickcep_session_id> --content-file /tmp/draft-<quickcep_session_id>.html …`
   - If `get-escalation` / brief includes `operator_attachments`, pass `--attachments` with the JSON array (`fileName`, `fileSize`, `url`).
   - **PDF rule:** Only vault-uploaded PDFs (in `allowed_attachment_urls`) may attach. Product assembly / `static.povison.com` PDFs → extract text into body; guard blocks PDF attachments otherwise.
   - Feishu thread **images** may appear in `operator_attachments` (poller auto-uploaded to QuickCEP CDN).
7. **terminal:** `apply-handoff --phase draft_ready` — tags + internal note
8. **terminal:** `write-event --event-type escalation_resumed`
9. **terminal:** `update-session-status --status draft_ready`

## apply-handoff example

```bash
python3 cs_bridge_tool.py apply-handoff --env LIVE --session-id <quickcep_session_id> \
  --phase draft_ready \
  --actions-taken "已合并飞书专家答复并保存草稿" \
  --operator-hint "<中文：下一操作员接手要点>"
```

**备注语言：** 所有 apply-handoff 文本字段必须使用简体中文（客户邮件草稿除外）。

## Pitfalls

**execute_code for bridge CLI** — Forbidden. ESC:17 used `subprocess.run(['python3','cs_bridge_tool.py',…])` → gateway approval stall. Use **terminal** per step.

**Wrong session id** — Use `quickcep_session_id` from escalation JSON, not CAL `session.id` (e.g. 1088).

**Ignoring operator_answer** — The Feishu reply is authoritative for policy exceptions.

**send-email** — Never auto-send; draft only.

**Upload-after-reply** — Expert uploaded after Feishu text reply → attachments missing from draft. SOP: upload first.

**Assembly PDF as attachment** — Forbidden. Use text in body; vault PDFs only via `--attachments`.

**Shared temp draft path** — Never use `/tmp/draft.html`; concurrent runs can overwrite content. Always use session-scoped paths (`/tmp/draft-<quickcep_session_id>.html`).

**Skipping apply-handoff** — No AI-草稿待审 tag or handoff note after resume.

## Examples

**Success:** Operator confirms OEKO-TEX certification → draft reflects that → apply-handoff draft_ready → draft_ready status.

**Success with vault PDF:** Expert uploads PDF via ESC upload link + replies in Feishu → resume brief includes `operator_attachments` → draft-save with `--attachments` → guard allows vault CDN URL.

**Failure:** Agent attaches `static.povison.com/.../assembly.pdf` → draft-save attachment guard exit 2 (`pdf_product_url`).

**Failure:** Agent uses execute_code to batch get-escalation + get-messages → blocked or stuck on approval.
