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
2. For each message, **resolve KOL identity using a multi-strategy match** (record both the chosen strategy and a confidence in [0, 1]):

   | Order | Strategy | Confidence on match |
   |---|---|---|
   | 1 | `cal.resolve_identity(aliases=[('gmail_thread_id', tid)])` matches a known identity (thread persisted from a prior outreach draft) | 1.00 |
   | 2 | Follow `In-Reply-To` / `References` headers; if any referenced message id is in `cal.kol_identity_alias` (kind=`gmail_message_id`) | 0.90 |
   | 3 | `cal.resolve_identity(aliases=[('email', from_addr)])` matches a known identity (KOL replied from a known address but in a new thread) | 0.80 |
   | 4 | Body mentions exactly one `@handle` that resolves via `cal.resolve_identity(aliases=[('handle', h)])` | 0.60 |
   | — | None of the above | 0.00 → escalate as `unknown_sender` and move on |

3. If `match_confidence < 0.7`, do NOT draft for this message; record a `kol_reply_history` row with `match_strategy='unmatched'` (or the partial match) and add to the escalate batch with reason `low_confidence_match`.

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
| `content_submission`| KOL submitted a draft video; body contains a URL on the platform whitelist (youtube/youtu.be/tiktok/instagram/bilibili/vimeo). ONLY valid when the KOL's current stage is `logistics.delivered` or later. |
| `declines`          | Says no, not a fit, not available. |
| `out_of_office`     | Auto-reply / away message. |
| `other`             | Anything else (questions, scheduling, off-topic). |

Rules:
- Confidence must reflect ambiguity (e.g. "sounds cool, what's the budget?" is `interested` ~0.6, `proposes_rate` ~0.4).
- If top intent confidence `< 0.7`, **do not draft**. Escalate (see Step 5).
- If top two intents are within 0.1 of each other, treat as ambiguous → escalate.

Record the intent + confidence as part of the Step 4b CAL `record_reply` row (fields `intent` and `confidence`). There is no separate per-card scratch; the latest `kol_reply_history` row is the authoritative `last_reply` for this identity.

### Step 4 — Route by intent
| Intent | Action |
|---|---|
| `interested` / `asks_materials` | Invoke `kol-outreach-product-pitch-email` skill with this card. |
| `proposes_rate` / `counter_offers` | Parse number + currency. If parse succeeds, invoke `kol-outreach-negotiation-email`. If parse fails, escalate. |
| `content_submission` | Invoke `kol-outreach-content-review` skill (extracts video URL + notifies operator; does NOT draft). |
| `declines` | Write a `closed_declined` CAL event for this identity. Draft no reply. Add to next escalate digest (info only). |
| `out_of_office` | Re-label message back to `kol-outreach/replied/ooo`. Do not draft. No escalate. |
| `other` | Escalate. |

After a successful draft (or `content_submission` routing), move the inbound message's label from `kol-outreach/pending-reply` to `kol-outreach/replied/<intent>` and mark read. This is the idempotency anchor: never process a message that is not under `pending-reply`.

### Step 4b — CAL audit (mandatory, fire-and-forget)
For every processed inbound message, write CAL **before** the label move (so a crash mid-step doesn't lose the reply):

1. `cal.record_reply(kol_identity_id=<resolved id or NULL>, gmail_message_id=<id>, gmail_thread_id=<tid>, from_addr=<addr>, received_at=<iso8601>, snippet=<≤200 chars>, body=<full body>, intent=<intent>, confidence=<intent confidence>, match_strategy=<chosen strategy from Step 1>, match_confidence=<from Step 1>, handled_action=<routed_skill|escalated|ignored>, card_id=NULL, campaign_id=<id>)`. (`card_id` is a legacy column; always NULL in v2.)
2. `cal.record_event(event_type='reply_received', stage=<KOL's current stage>, sub_status='reply_classified', actor='cron:dispatcher', payload={intent, intent_confidence, match_strategy, match_confidence, gmail_message_id})`.
3. On escalation: `cal.record_escalation(reason=<low_confidence_reply|low_confidence_match|unknown_sender|parse_failed|other>, kol_identity_id=<or null>, card_id=NULL, campaign_id=<or null>, classifier_confidence=<x>, ai_recommendation=<one-line>)`.

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

Append each new `draft_id` to the corresponding identity's `notified_drafts` set via a `cal.record_event(event_type='draft_notified', stage=<stage>, sub_status=<sub_status>, payload={draft_id})`. Idempotency: before notifying, scan the identity's prior `draft_notified` events; skip any draft id already listed.

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
- **Never write a `sent_*` sub_status into CAL.** Only the human (by sending the Gmail draft from their mailbox) advances state to a `sent_*` event. The dispatcher only writes `drafted_*` / `closed_declined` events.

## Pitfalls
- Do not treat empty bodies (quoted-only reply) as `interested`; classify as `other` and escalate.
- Do not chain dispatcher runs — if a draft skill fails, log the error on the card and continue with the next message; do not retry inline.
- Do not "merge" multiple inbound messages on the same thread into one classification; classify only the newest unread message, but feed prior context to the draft skills.
- Do not rely on the KOL's prior intent; classify fresh every run.
- Do not skip the `notifier` channel just because chat already received the message — both are required when a `notifier` is configured.
