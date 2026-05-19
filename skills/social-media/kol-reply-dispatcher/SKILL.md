---
name: kol-reply-dispatcher
description: Cron-triggered KOL reply listener. Every 10 minutes, pulls unread Gmail replies under kol-outreach/pending-reply, classifies intent, routes to the right draft skill (product-pitch / negotiation / decline), and escalates low-confidence cases to chat. Never sends; never auto-decides budget.
trigger: Runs on cron `*/10 * * * *` under profile `outreach-operator`. Also runs on demand when the user types "check KOL replies".
tags: ["kol", "outreach", "dispatcher", "reply", "cron", "gmail", "draft"]
---

## Goal
Keep KOL outreach moving without human polling: detect new replies in Gmail, classify them, and prepare the next drafted email. Hand low-confidence or out-of-policy cases to the human via chat. Drafts only — the dispatcher never sends mail and never modifies budget config.

## Runtime Contract
- Frequency: every 10 minutes via Hermes `cronjob`.
- Profile: `outreach-operator`.
- Idempotency: a message is processed at most once, tracked via the Gmail label flow described below.
- Hard stop: if the dispatcher cannot read the campaign config for any reply's `campaign_id`, escalate that thread and continue with the rest.

## Procedure

### Step 1 — Pull unread inbound replies
1. Query Gmail for messages with label `kol-outreach/pending-reply` and `is:unread`.
2. For each message, resolve its thread, then look up the Kanban card whose `gmail_thread_id` matches. If no card matches, escalate (`unknown thread: <thread_id>`) and move on.

### Step 2 — Load campaign context
1. Read `campaign_id` from the card; load `~/.hermes/kol-outreach/<campaign_id>/config.yaml`.
2. Confirm `mode` (TEST/LIVE) and budget fields exist. If config missing/corrupt, escalate.

### Step 3 — Classify intent
Use the LLM to assign exactly one intent + a confidence score in [0, 1]. Allowed intents:

| Intent | Definition |
|---|---|
| `interested`        | Positive, wants to know more, no price asked. |
| `asks_materials`    | Asks for product info / catalog / samples. |
| `proposes_rate`     | Provides a specific number as a fee ask. |
| `counter_offers`    | Responds to a prior number with a different number. |
| `declines`          | Says no, not a fit, not available. |
| `out_of_office`     | Auto-reply / away message. |
| `other`             | Anything else (questions, scheduling, off-topic). |

Rules:
- Confidence must reflect ambiguity (e.g. "sounds cool, what's the budget?" is `interested` ~0.6, `proposes_rate` ~0.4).
- If top intent confidence `< 0.7`, **do not draft**. Escalate (see Step 5).
- If top two intents are within 0.1 of each other, treat as ambiguous → escalate.

Record the intent + confidence on the card under `last_reply`:

```yaml
last_reply:
  message_id: <id>
  received_at: <iso8601>
  intent: <intent>
  confidence: <0..1>
```

### Step 4 — Route by intent
| Intent | Action |
|---|---|
| `interested` / `asks_materials` | Invoke `kol-outreach-product-pitch-email` skill with this card. |
| `proposes_rate` / `counter_offers` | Parse number + currency. If parse succeeds, invoke `kol-outreach-negotiation-email`. If parse fails, escalate. |
| `declines` | Set `status: closed_declined`. Draft no reply. Add to next escalate digest (info only). |
| `out_of_office` | Re-label message back to `kol-outreach/replied/ooo`. Do not draft. No escalate. |
| `other` | Escalate. |

After a successful draft, move the inbound message's label from `kol-outreach/pending-reply` to `kol-outreach/replied/<intent>` and mark read. This is the idempotency anchor: never process a message that is not under `pending-reply`.

### Step 5 — Escalate low-confidence / policy cases
Accumulate an escalate list across this run. At end of run, post **one** chat message:

```
🛎️ KOL replies need a human (<campaign_id>): <N> case(s)
- @<handle>  intent=<intent> conf=<x.xx>  reason=<low_confidence|ambiguous|unparseable_price|other>
  Gmail: https://mail.google.com/mail/u/0/#inbox/<thread_id>
- ...
Dispatcher took no action on these threads.
```

If a `notifier` channel is configured, also send the count + first 3 handles to that channel.

### Step 6 — Batch "drafts ready" notification
After all routing is done, post **one** consolidated chat message with all newly created drafts in this run:

```
✉️ <N> reply draft(s) ready in Gmail.
Campaign: <campaign_id>   Mode: TEST/LIVE
By intent:
  - product_pitch: <count>  label kol-outreach/pending/product_pitch
  - negotiation:   <count>  label kol-outreach/pending/negotiation
Drafts:
  - @<handle>  intent=<intent>  draft_id=<r-...>
  - ...
Review in Gmail; agent will not send.
```

Append each new `draft_id` to the corresponding card's `notified_drafts` to prevent re-notification.

### Step 7 — Exit
Return a structured run report:

```yaml
processed: <N>
drafted:
  product_pitch: <n1>
  negotiation:   <n2>
escalated: <n3>
errors: <n4>
```

If `errors > 0`, attach a short error list to the chat escalate message.

## Hard Rules
- **Never send.** Drafts only.
- **Never modify** `~/.hermes/kol-outreach/<campaign_id>/config.yaml`.
- **Never bypass the 0.7 confidence threshold.**
- **Never process a message twice.** The `pending-reply → replied/<intent>` label move is the single source of truth.
- **Never escalate per-message in real time.** Batch into one chat message per run.
- **Never start a new Gmail thread** from this skill; always reply on the existing `gmail_thread_id`.
- **Never change Kanban `status` to `sent_*`** — only the human (by sending the draft) advances to a `sent_*` state. The dispatcher only writes `drafted_*` / `closed_declined`.

## Pitfalls
- Do not treat empty bodies (quoted-only reply) as `interested`; classify as `other` and escalate.
- Do not chain dispatcher runs — if a draft skill fails, log the error on the card and continue with the next message; do not retry inline.
- Do not "merge" multiple inbound messages on the same thread into one classification; classify only the newest unread message, but feed prior context to the draft skills.
- Do not rely on the KOL's prior intent; classify fresh every run.
- Do not skip the `notifier` channel just because chat already received the message — both are required when a `notifier` is configured.
