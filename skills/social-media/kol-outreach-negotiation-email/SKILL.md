---
name: kol-outreach-negotiation-email
description: Draft (never send) a negotiation reply to a KOL who has named a rate. Reads the campaign budget floor and refuses + escalates when the ask is too close to the absolute floor. Never commits to a price without explicit human approval in the Gmail draft.
trigger: When the reply dispatcher classifies a KOL reply as intent `proposes_rate` or `counter_offers` with confidence >= 0.7, and the KOL's latest CAL `kol_conversation_events.stage` is in {`product_pick`, `negotiation`}.
tags: ["kol", "outreach", "email", "negotiation", "gmail", "draft", "budget"]
---

## Goal
Draft one reply-on-thread Gmail draft that moves a price negotiation one step forward — accept, counter, or politely decline — while strictly respecting the campaign's `absolute_floor`. The draft must always leave the final go / no-go decision to the human reviewer in Gmail.

## Inputs
- `campaign_id` + config path.
- `kol_identity_id` (from CAL). The caller must also pass `gmail_thread_id` (from `kol_identity_alias` where `kind='gmail_thread_id'`).
- KOL's reply text containing a price ask.
- Parsed `requested_amount` and `requested_currency` (caller is responsible for extracting these — if it cannot, escalate).

## Procedure

### Step 1 — Load budget
1. Read `config.yaml` → `budget_per_kol`, `absolute_floor`, `currency` (default USD).
2. If currencies mismatch, escalate to chat with the FX context; do not auto-convert in this skill.

### Step 2 — Decide the move
Let `R = requested_amount`, `B = budget_per_kol`, `F = absolute_floor`. All comparisons in the same currency.

Evaluate in this order; the first matching row wins.

| # | Condition | Action |
|---|---|---|
| 1 | `R` not parseable / multiple amounts in reply | **Escalate**; do not draft. |
| 2 | `R > absolute_floor * 0.95` (i.e. the ask is too close to or above our floor) | **Refuse + escalate.** Use the refuse-and-hold template and post a chat escalate. Never counter. |
| 3 | `R <= B` (within per-KOL budget) | Draft an **accept** reply (human still sends). |
| 4 | `B < R <= F * 0.95` (above per-KOL budget but safely below floor) | Draft a **counter** at `min(B, floor(F * 0.90))`. |

Floor semantics (per campaign config): `absolute_floor` is the hard wall the agent must not commit at or above. The 0.95 buffer keeps a margin so the human, not the agent, decides anything that approaches the wall.

### Step 3 — Compose draft body (English, reply on thread)

Accept template:
```
Hi <first_name_or_handle>,

<amount currency> works on our side. I'll line up the brief and timeline and circle back with next steps shortly.

<brand_signature>
```

Counter template:
```
Hi <first_name_or_handle>,

Thanks for sharing your rate. For this campaign we're working with a budget around <counter_amount currency>. Would that range still work for you? Totally understand if not.

<brand_signature>
```

Refuse-and-hold template (used when escalating):
```
Hi <first_name_or_handle>,

Really appreciate the proposal. Let me check internally on budget before I get back to you with a clear answer — I'll follow up within a few business days.

<brand_signature>
```

Hard content rules:
- **No contract terms, no usage rights specifics, no payment timing** in this skill. Those belong to a human reviewer.
- **One concrete number per draft.** Never list a range like "$500-$800".
- **No "best and final"** language.

### Step 4 — Create draft on thread
1. `gmail drafts.create` with `threadId = gmail_thread_id`.
2. TEST MODE rewrite same as other skills (`to = test_mode_to`, prepend `Intended recipient:`).
3. Label: `kol-outreach/pending/negotiation`.

### Step 5 — (removed in v2) Persist state via CAL only
No Kanban card update. The draft id, negotiation decision, last request/counter amount, `stage='negotiation'`, and the appropriate `sub_status` are all written by Step 5b's CAL calls (`cal.record_negotiation` + `cal.record_draft` + `cal.record_event`). The KOL Ops Console derives the working state from these rows.

### Step 5b — CAL audit (mandatory, fire-and-forget)
Write negotiation history, draft, and event to CAL.

1. `cal.record_negotiation(kol_identity_id, decision=<accept|counter|refuse_escalate>, kol_request_amount=R, currency=<...>, agent_counter_amount=<counter or null>, decision_reason='<one-line reason>', budget_per_kol_at_time=B, absolute_floor_at_time=F, card_id=NULL, campaign_id=<id>, product_sku=<from caller>)`. (`card_id` is a legacy column; always NULL in v2.)
2. `cal.record_draft(stage='negotiation', sub_status='<accept_drafted|counter_drafted|refuse_escalated>', ...)` with full subject/body and `context_snapshot`:
   ```json
   {
     "kol_request_amount": R,
     "currency": "<...>",
     "agent_counter_amount": <number or null>,
     "decision": "<accept|counter|refuse_escalate>",
     "budget_per_kol": B,
     "absolute_floor": F,
     "floor_buffer": 0.95,
     "prior_reply_quote": "<≤200-char excerpt from KOL's price message>",
     "current_stage": "negotiation",
     "sub_status_at_time": "<accept_drafted|counter_drafted|refuse_escalated>",
     "triggered_by": "<chat|web|cron>"
   }
   ```
3. `cal.record_event(event_type='emailed_negotiation', stage='negotiation', sub_status=<sub_status>, actor=<from caller>, payload={draft_id, decision})`.
4. If `decision == 'refuse_escalate'`: also `cal.record_escalation(reason='floor_violation', kol_identity_id=<id>, card_id=NULL, campaign_id=<id>, ai_recommendation='hold and ask human; do not auto-counter')`.

### Step 6 — Escalate when required
If `decision == refuse_escalate`, post **one** chat message:

```
⚠️ Negotiation hit floor zone for @<handle> (<campaign_id>)
Requested: <R currency>   Floor: <F currency>   Per-KOL budget: <B currency>
Drafted a polite hold reply: <draft_id> (label kol-outreach/pending/negotiation)
Decide: accept anyway / counter manually / drop KOL?
```

For every other decision, **do not** post a per-KOL chat message; the orchestrator/dispatcher will batch a "drafts ready" summary.

## Hard Rules
- Never draft a price at or below `absolute_floor`. The floor is a hard wall.
- Never send; drafts only.
- Never auto-accept above `budget_per_kol` even when the gap is small; counter or escalate.
- Never combine accept + counter language in one draft.
- Never write a number outside the same currency as the config.

## Pitfalls
- Do not parse the KOL's reply with loose regex (e.g. "$500-$800" → average). Multi-number = escalate.
- Do not silently round counters; round to a clean number (`max(B, F * 1.10)` then round up to nearest $50 if currency is USD).
- Do not promise gifted product as a sweetener in this email; that's a separate decision.
- Do not assume the KOL's currency from their location; require the caller to provide currency explicitly.
