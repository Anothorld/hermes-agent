---
name: povison-cs-orchestrator-flow
description: Inbound QuickCEP email workflow for Povison CS automation.
trigger: Gateway brief contains cs_inbound_process or new QuickCEP email enqueue.
tags: [povison, customer-service, quickcep, orchestrator]
---

# Povison CS Orchestrator Flow

Processes one inbound QuickCEP **email** session: classify, auto-handle or escalate, write draft, apply lifecycle tags and internal notes. **Email channel only** — do not process web chat, SMS, or phone sessions.

## When to Use

- Watcher enqueued an **email** session (`# cs_inbound_process` in gateway brief)
- Manual re-run for a stuck QuickCEP **email** session (Console relaunch; non-email sessions are rejected)

## Prerequisites

- `cs-ops-bridge` running (`serve.py` on port 8081)
- `povison-cs` gateway with API server (port 8643)
- Profile `.env` includes `HERMES_CS_OPS_BRIDGE_KEY`, `CS_OPS_BRIDGE_BASE`, `QUICKCEP_EMAIL`, `QUICKCEP_PASSWORD` (run `setup_cs_ops_env.py --write-profile-env`)
- QuickCEP **AI客服** tags created (see bridge `config/session_tag_map.yaml`)
- Session **intentionTags** must include **产品咨询** or **物流咨询** (watcher intent gate; other intents are not auto-launched)

## Tool paths (mandatory — do not guess)

Read `# agent_tool_paths` from the gateway brief **or** `get-dispatch-context` → `agent_tool_paths`:

- **`cs_bridge_tool` only** — all bridge and QuickCEP operations (`get-messages`, `draft-save`, `apply-handoff`, …)
- **`terminal` tool only** — one `python3 <cs_bridge_tool> …` command per terminal call
- **Never `execute_code` / subprocess.run** to batch bridge CLI steps (gateway blocks this on `povison-cs:*` runs)

Example (each line is a separate **terminal** tool call):

```bash
python3 <cs_bridge_tool> get-messages --env LIVE --session-id <id>
python3 <cs_bridge_tool> draft-save --env LIVE --session-id <id> --content-file /tmp/draft-<id>.html --subject "Re: ..." --receiver "customer@email.com"
python3 <cs_bridge_tool> apply-handoff --env LIVE --session-id <id> --phase processing ...
```

Do **not** call `quickcep_cli` directly — use `cs_bridge_tool get-messages` instead of `quickcep_cli messages`.

When using `--content` in shell, **single-quote** drafts that contain `$` (e.g. `--content 'refund $200 after delivery'`). Double quotes turn `$200` into `00`.

## Procedure

1. `skill_view(name='povison-cs-orchestrator-flow')` — this skill
2. Bridge CLI (use absolute path from `# agent_tool_paths` / dispatch-context):
   - `get-dispatch-context --env LIVE --session-id <id>`
   - Read `orders` from dispatch-context. For logistics intent + non-empty orders, dispatch-context now includes `tracking` prefill summaries from Povison order-track API (`status`, `trackingNumber`, `earliestEdd`, `latestEdd`).
3. `get-messages --env LIVE --session-id <id>`
4. `classify-intent` via bridge CLI with email subject + body
5. **`apply-handoff --phase processing`** — tags (AI-处理中 + inquiry) + start note
6. **If route=auto_handle:**
   - Product → `povison-product-lookup` skill
   - Logistics → `povison-order-track` skill (if `dispatch-context.tracking.enabled=true`, use prefetched `tracking.summaries` first; only call additional lookup when summary is missing)
   - Compose English reply → **`cs_bridge_tool draft-save`** (auto join-chat) with `--content` or `--content-file`, `--subject`, `--receiver` (never `send-email`)
   - **`apply-handoff --phase draft_ready`** — AI-草稿待审 + 待客户回复 + completion note
   - `update-session-status --status draft_ready`
7. **If route=escalate or review→escalate:**
   - `skill_view(name='povison-feishu-escalation')` for message templates
   - After **`get-messages`**, write:
     - **`--email-summary`** — 1–3 句**简体中文**，概括客户与本次升级相关诉求
     - **`--email-quote`** — 1–3 句**原文摘录**（保持客户来信语言，如英文），只摘与问题对应的部分，不要整封复制
   - **`open-escalation --customer-email "<from messages>" --email-summary "<中文摘要>" --email-quote "<original quote>" --reason "..." --urgency low|medium|high --question "..."`**
     - 原文含 `$` 或多行 → `--email-quote-file /tmp/quote.txt`
     - Bridge **auto-posts** to Feishu AI客服后援 (do NOT use `send_message` on api_server runs)
   - Read `feishu.thread_id` from the JSON response
   - **`apply-handoff --phase awaiting_expert --feishu-thread-id <thread_id>`**
   - `update-session-status --status awaiting_expert`
8. **On unrecoverable error:** `apply-handoff --phase failed` then `update-session-status --status failed`

## apply-handoff fields

| Flag | Purpose |
|------|---------|
| `--customer-need` | 1–3 句中文：客户想要什么（订单/产品/物流等） |
| `--actions-taken` | 中文：已完成的**业务**动作（查单、查品、保存草稿、升级等） |
| `--follow-up` | 中文：客服同事下一步该做什么 |
| `--operator-hint` | 中文：一行接手摘要（给客户什么、注意什么） |
| `--classify-json` | JSON from classify-intent |
| `--feishu-thread-id` | Required for awaiting_expert phase |

**内部备注原则：** 写给**客服同事**看，只写与会话相关的业务信息。禁止写 CLI 参数（如 `--content-file`）、系统组件（gateway/bridge/日志）、`run_id`/`message_id` 等工程细节。Bridge 会自动过滤常见技术用语。

**Valid `--phase` values only:** `processing`, `draft_ready`, `awaiting_expert`, `failed`, `reviewed`, `followup_while_busy`, `operator_sent`. Never invent names like `completed` or `processed_by_human` — use `reviewed` for human-closed cases.

**语言：** 以上写入 QuickCEP **内部备注**的字段必须全部使用**简体中文**，不要英文。客户邮件草稿（`draft-save --content`）仍用英文。

Bridge applies QuickCEP tags and `add-note` automatically — **do not** call `tags-add` / `add-note` directly.

## Pitfalls

**execute_code for bridge CLI** — Forbidden. Do not wrap `cs_bridge_tool` in Python/subprocess; use **terminal** once per step. The guard blocks `execute_code` that references `cs_bridge_tool`.

**Wrong script path** — Never search for `quickcep_cli.py`. Use `cs_bridge_tool` path from the brief or dispatch-context only.

**Direct quickcep_cli** — Forbidden in automation. Use `cs_bridge_tool get-messages` and `cs_bridge_tool draft-save`.

**Skipping join-chat before draft** — `joinChat` is handled by the watcher at launch time (fail-soft); the agent does not need to call it manually. The default `draft-save` writes to CAL (no joinChat). The legacy QuickCEP path (`--legacy-quickcep-draft`) still auto-calls `join-chat` before save.

**Shared temp draft path** — Never use `/tmp/draft.html` across sessions. Always use session-scoped paths like `/tmp/draft-<session_id>.html` to avoid cross-session draft contamination under concurrent runs.

**Fake HTML wrappers** — Do not save `<html><body>…</body></html>` with plain-text newlines; QuickCEP shows one block. Use plain text in `--content-file` (auto `<p>/<br>`) or real `<p>` tags.

**draft-save missing flags** — Requires `--content` or `--content-file`; add `--subject` and `--receiver` for email drafts.

**Dollar amounts in draft-save** — Never pass `--content "…$200…"` in double quotes; shell expands `$2` and leaves `00`. Use `--content-file` or single quotes: `--content '…$200…'`.

**Skipping apply-handoff** — Operators lose tags and internal notes; always call at processing start and branch end.

**English in internal notes** — `--customer-need`, `--actions-taken`, `--follow-up`, `--operator-hint`, and `--error` must be Simplified Chinese. English is only for customer-facing `draft-save --content`.

**Technical details in internal notes** — Notes are for CS operators, not engineers. Do not paste CLI flags, gateway/bridge errors, or log hints. Example failed note: `--actions-taken "已查询订单物流，但未能生成回复草稿，需人工回复"` — not `--error "draft-save failed: domain parsed as command"`.

**Skipping open-escalation summary/quote** — `--email-summary` and `--email-quote` are **required** for Feishu notify. Bridge returns 422 if either is missing.

**Wrong language in summary** — `--email-summary` must be **Simplified Chinese**. `--email-quote` must stay in the **customer's original language** (e.g. English).

**Full email in quote** — `--email-quote` must be a **focused partial quote**, not the entire inbound message or reply chain.

**send-email to customer** — Forbidden. Use `draft-save` only.

**Direct QuickCEP tag IDs** — Never hardcode; bridge owns tag map.

**Invalid apply-handoff phase** — Only `processing`, `draft_ready`, `awaiting_expert`, `failed`, `reviewed`, `followup_while_busy`, `operator_sent`. Do not use `completed`, `processed_by_human`, or other invented names (bridge returns 422/400, not success).

**Wrong intent auto-launched** — Watcher only processes `intentionTags` 产品咨询 / 物流咨询. Refund/payment/legal emails stay human-only unless Console relaunch.

## Examples

**Success (product inquiry):** classify → processing handoff → product lookup → draft-save → draft_ready handoff → status draft_ready.

**Failure (skipped send_message):** escalation marked in chat only; operators never see request — always execute send_message.

**Failure (skipped apply-handoff):** draft saved but no AI-草稿待审 tag — operators cannot find pending drafts.
