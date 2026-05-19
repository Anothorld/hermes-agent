---
name: kol-outreach-orchestrator-flow
description: End-to-end KOL outreach orchestrator. Takes product brief + SKU whitelist + budget, runs Instagram KOL discovery, asks user to approve a shortlist in chat, then writes Gmail drafts (never sends) and notifies the user. Reviews happen in Gmail; agent only drafts.
trigger: When the user wants to start, launch, kick off, or run a KOL outreach campaign for a furniture product — e.g. "run outreach", "start the <SKU> campaign", "launch KOL outreach for <product>", "open a new campaign", "find KOLs and prep emails", or any message that provides a product brief / SKU pool / budget and asks the agent to manage the pipeline through to drafted emails. This skill OWNS the campaign lifecycle; do not bypass it with ad-hoc Kanban cards.
tags: ["kol", "outreach", "orchestrator", "gmail", "draft", "campaign"]
---

## Goal
Run a complete KOL outreach campaign for a furniture product, end-to-end, while keeping the human in control through **two approval surfaces only**: (a) a single chat approval of the KOL shortlist, and (b) the Gmail drafts inbox for every outbound email. The agent **never sends mail** — it only creates Gmail drafts and notifies the user that drafts are ready.

Hard defaults:
- **Draft-only**: every outbound message is created via `gmail drafts.create` / `drafts.update`. Never call any `send` API.
- **TEST MODE is default**: until the user types `LIVE MODE` in chat, every draft's `to` field is rewritten to the campaign's `test_mode_to` address.
- **Notify proactively**: agent must push a chat notification when the shortlist is ready and when each draft batch finishes. Do not write drafts silently.
- **Discovery is delegated**: KOL search runs through the `instagram-kol-discovery` skill; this skill never re-implements crawling.
- **One campaign at a time**: each invocation owns exactly one `campaign_id`.

## Inputs
The user (or upstream skill) must provide:

- **Product brief** — category, key features, research/persona doc if available.
- **SKU whitelist / 选品池** — explicit list of product URLs or SKU ids; nothing outside this list may be linked in any later email.
- **Budget** — `budget_total`, `budget_per_kol`, `absolute_floor` (USD by default). `absolute_floor` is the **absolute ceiling** of the refusal zone — the maximum the agent may ever counter at; anything at or above it must be escalated to a human. Typical relationship: `budget_per_kol < absolute_floor < budget_total`.
- **Headcount target** — number of KOLs to engage (default 5-10).
- **Campaign id** — short slug, e.g. `seb8008-spring`. If missing, derive from product and ask user to confirm.
- **Test inbox** — `test_mode_to` email for TEST MODE drafts.

If any required field is missing, ask the user once in chat with a single consolidated question. Do not start crawling before all required fields are present.

## Campaign Config Persistence
Persist the resolved inputs to `~/.hermes/kol-outreach/<campaign_id>/config.yaml`:

```yaml
campaign_id: seb8008-spring
product_brief_path: ...
sku_whitelist:
  - https://example.com/sku/seb8008
budget_total: 12000
budget_per_kol: 1500
absolute_floor: 600
headcount_target: 8
test_mode_to: tester@example.com
mode: TEST            # or LIVE, only after explicit user confirmation
created_at: 2026-05-19T10:00:00Z
```

Every downstream skill (initial-email, product-pitch-email, negotiation-email, reply-dispatcher) reads this file. Never duplicate budget or whitelist values inside SKILL prompts.

> Use `config.yaml.example` (next to this SKILL.md) as the starting template.
> Operational setup (Gmail labels, cron registration, TEST MODE dry-run, rollback)
> lives in `SETUP.md` next to this SKILL.md. Read it before the first campaign.

## Trigger Mode (chat | web | cron)
This skill accepts a `triggered_by` parameter at invocation. It defaults to `chat` for free-form chat invocations. The external KOL Ops Console invokes via Gateway `POST /v1/runs` with `triggered_by=web` baked into the instructions.

Mode branch (single point of divergence — keep it small):
- `chat` — Step 3 posts the shortlist approval prompt and **waits** for the user's chat reply with one of the four allowed verbs.
- `web`  — Step 3 writes the shortlist to disk, writes a `shortlist_ready` event to CAL, then **exits**. The operator approves through `POST /api/plugins/kol-ops-bridge/campaigns/{id}/approve-shortlist`; a fresh agent session resumes from Step 4 when the approval lands.
- `cron` — same as `chat` but treats absence of a chat user as a hard error (cron should not block on chat approval).

The `triggered_by` value flows into every CAL write as `actor=<chat|web|cron>` (or a more specific value like `web:operator`).

## 8-Stage State Machine
KOL cards live in exactly one of these 8 stages, with sub-status detail. The Kanban card's `stage` + `sub_status` MUST stay in sync with the latest `kol_conversation_events` row.

| # | stage | typical sub_status values |
|---|---|---|
| 1 | `discovered`        | `pool_pending_approval` → `approved` / `rejected` |
| 2 | `outreach`          | `email_missing` / `initial_drafted` / `initial_sent` / `reply_received` |
| 3 | `product_pick`      | `pitch_drafted` / `pitch_sent` / `sku_selected` |
| 4 | `negotiation`       | `accept_drafted` / `counter_drafted` / `refuse_escalated` / `accepted` |
| 5 | `contract`          | `pending` / `sent_for_signature` / `signed` / `declined` |
| 6 | `logistics`         | `pending` / `address_collected` / `tracking_filled` / `in_transit` / `delivered` |
| 7 | `content_delivery`  | `submitted_v<n>` / `revision_requested_v<n>` / `approved_v<n>` |
| 8 | `closed`            | `closed_success` / `closed_declined` / `closed_abandoned` |

The legacy `status` field is retained as a free-form pointer for backwards compatibility, but the canonical truth is `(stage, sub_status)`.

## Procedure

### Step 1 — Resolve campaign config
1. Read or create `~/.hermes/kol-outreach/<campaign_id>/config.yaml`.
2. Validate: SKU whitelist non-empty, `budget_per_kol > 0`, `budget_per_kol < absolute_floor <= budget_total`, `test_mode_to` is a valid email.
3. Fail fast in chat if validation fails. Do not proceed.
4. **CAL**: `cal.record_event(event_type='campaign_started', stage='discovered', sub_status=null, actor=<triggered_by>, product_sku=<from config>, campaign_id=<id>, kol_identity_id=null, payload={mode, budget_total, budget_per_kol, absolute_floor, headcount_target})` — use the special identity id `0` (system event) by recording on a campaign-scoped event without an identity. If CAL requires an identity, skip this write; the bridge backend infers campaign start from the first per-KOL event.

### Step 2 — Run discovery
1. Invoke the `instagram-kol-discovery` skill with the product brief and budget context.
2. Expect its output to be a grouped Markdown document (groups by product feature / selling point, 3-5 creators each, including data + creator type + recommendation reason).
3. Persist the raw discovery output to `~/.hermes/kol-outreach/<campaign_id>/shortlist.md`.

### Step 3 — Notify user for shortlist approval (mandatory)
In `triggered_by=web` mode, do NOT post the chat prompt. Instead:
1. `cal.record_event(event_type='shortlist_ready', ...)` for the campaign.
2. Exit the agent run cleanly. The Web operator approves via `POST /api/plugins/kol-ops-bridge/campaigns/{id}/approve-shortlist`; a fresh agent session resumes at Step 4 when the approval webhook arrives.

In `triggered_by=chat` mode (default), post **one** chat message using this fixed template, then **stop and wait** for the user's reply. Do not move on without explicit approval.

```
✅ KOL shortlist ready — <N> candidates across <M> selling-point groups.
Campaign: <campaign_id>   Mode: TEST   Test inbox: <test_mode_to>
Please review and reply with ONE of:
  - approve all
  - approve <group letters>   (e.g. approve A,C)
  - approve <handles>         (e.g. approve kathypicos, make.one.studio)
  - reject <handles>
Full list ↓
<grouped markdown from discovery>
```

If a `notifier` channel is configured (Telegram / Discord / Slack via Hermes gateway), also send the first 3 lines to that channel. Never duplicate the full shortlist into external channels (privacy).

### Step 4 — Parse approval and build per-KOL index cards
1. In `web` mode, the approval set arrives via the bridge endpoint's `selected_handles`. In `chat`/`cron` mode, parse the user's reply against the four allowed verbs. If the reply is ambiguous, ask once more with the same template; never guess.
2. For each approved handle:
   - **Upsert KOL identity in CAL** first: `kol_identity_id = cal.upsert_identity(handle=<handle>, primary_email=<email or null>, region=<from discovery>, creator_type=<from discovery>, env=<TEST|LIVE>)`. The same identity id MUST be reused for all later events on this handle (one identity, many cards across products).
   - Create one Kanban index card on board `kol-outreach`, title `kol:<handle>`.
   - Card body YAML:

     ```yaml
     campaign_id: <campaign_id>
     product_sku: <from config>
     kol_handle: <handle>
     kol_identity_id: <from cal.upsert_identity>
     email: null
     selling_point_group: <group letter or label>
     creator_type: <from discovery row>
     match_score: <number>
     showcase_score: <number>
     final_fit: <number>
     draft_ids:
       initial: null
       product_pitch: null
       negotiation: null
     gmail_thread_id: null
     status: shortlisted
     stage: outreach
     sub_status: email_pending
     env: <TEST|LIVE>
     notified_drafts: []
     ```

   - **CAL**: `cal.record_event(event_type='approved', stage='discovered', sub_status='approved', actor=<triggered_by>, kol_identity_id=<id>, card_id=<id>, product_sku=<...>, campaign_id=<...>, payload={selling_point_group, creator_type, final_fit})`. Old `status` enum is retained for back-compat; canonical truth is `(stage, sub_status)` from the 8-stage table above.

### Step 5 — Email discovery (lightweight)
For each approved KOL with `email: null`:
1. Inspect bio link, public website, link-in-bio aggregator, public business contact.
2. If a public business email is found, record it on the card with `email_source` and `email_confidence` (0-1).
3. If no public email is found within reasonable effort, mark the card `status: blocked_no_email` and include it in the next batch notification's escalate list. Do **not** scrape paid or private sources.

### Step 6 — Draft initial outreach emails (delegated)
For each KOL with an `email` and no `draft_ids.initial`:
1. Invoke the `kol-outreach-initial-email` skill, passing the campaign config path and the KOL card id.
2. Receive a `draft_id` back; write it into the card's `draft_ids.initial` and set `status: drafted_initial`.

### Step 7 — Notify user that drafts are ready (mandatory)
After **all** initial drafts for this batch are written, post **one** chat message:

```
✉️ <N> initial draft(s) ready in Gmail for review.
Campaign: <campaign_id>   Mode: TEST
Label: kol-outreach/pending/initial
Direct link: https://mail.google.com/mail/u/0/#label/kol-outreach%2Fpending%2Finitial
Drafts:
  - @<handle1>  (group <X>)  draft_id=<r-...>
  - @<handle2>  (group <Y>)  draft_id=<r-...>
Escalate (no email found):
  - @<handle3>
Review in Gmail; agent will not send.
```

After notifying, append each `draft_id` to the card's `notified_drafts`. Never re-notify the same draft.

### Step 8 — Hand off to dispatcher
1. Confirm `kol-reply-dispatcher` cronjob is registered (every 10 minutes by default). If not, register it.
2. Tell the user, in chat, that follow-up replies will be handled automatically by the dispatcher and that the next surface they will see is more drafts (or escalate notifications) in Gmail / chat.

### Step 9 — End of orchestrator run
Orchestrator returns. Subsequent rounds (product pitch / negotiation) are triggered by the dispatcher, not by this skill.

## Notification Rules

| Trigger | Channel | Required content |
|---|---|---|
| Shortlist ready | chat (primary) + notifier (header only) | counts, mode, fixed approval verbs, full markdown in chat |
| Batch drafts ready | chat (primary) + notifier (header only) | label, Gmail link, per-draft handle / group / `draft_id`, escalate list |
| Escalate (any) | chat + high-priority notifier | KOL, reason, agent suggestion (continue / drop / human decide) |

Notifications must be **idempotent**: never re-notify a `draft_id` already in `notified_drafts`.

## Hard Rules

- **Do NOT recreate the v1.1 Kanban review-gate pattern.** Specifically: do not create any task titled `campaign anchor`, `campaign root`, `review campaign brief and assumptions`, `review creator shortlist`, `safety / mode config`, `shortlist creator discovery`, `humanize ... draft`, `review initial outreach email`, `send initial outreach email`, or any per-pipeline-step gate. Do not assign tasks to `kol-scout`. The only Kanban cards this system creates are per-KOL index cards (`title: kol:<handle>`) described in Step 4. Any v1.1 docs found in `docs/_deprecated/` or elsewhere are reference-only and must be ignored.
- Never invoke `gmail.send`, `messages.send`, or any send-equivalent. Drafts only.
- Never bypass the shortlist approval step, even when discovery returns ≤ 3 candidates.
- Never read user data outside `~/.hermes/kol-outreach/<campaign_id>/` and the explicitly provided brief.
- Never write to the Kanban card body without preserving existing fields; merge, do not replace.
- TEST MODE is mandatory until the user types exactly `LIVE MODE` in chat for this `campaign_id`. Switching back to TEST is allowed at any time with `TEST MODE`.

## Pitfalls
- Do not start email discovery before the user approves the shortlist.
- Do not notify per draft; batch by intent (`initial`, `product_pitch`, `negotiation`).
- Do not silently retry a failed draft; surface the error in the next notification with the failing KOL.
- Do not link any SKU outside the campaign whitelist, even if the KOL asked for it; escalate instead.
- Do not store secrets (Gmail tokens, API keys) inside the campaign config; rely on Hermes secret manager / env vars.
- Do not parse the user's approval reply with fuzzy LLM matching; only accept the four allowed verbs. Ambiguous = ask again.
