---
name: kol-outreach-initial-email
description: Draft (never send) a first-contact English email to one Instagram KOL for a furniture brand collab. Reads campaign config for brand voice + test inbox; produces a short personalized pitch with no price, no attachments, no SKU links, only a soft CTA. Returns the Gmail draft id.
trigger: When the orchestrator (or user) needs the very first outreach email to a single shortlisted KOL whose business email is known and who has no prior thread. Not for replies, not for product pitches, not for negotiation.
tags: ["kol", "outreach", "email", "initial", "gmail", "draft"]
---

## Goal
Create exactly **one** Gmail draft addressed to one KOL, introducing the brand and proposing a possible collaboration. The email must be friendly, specific, short (≤ 130 words body), and zero-commitment. It must **not** propose a price, list SKUs, or attach files.

## Inputs (from caller)
- `campaign_id` and the path to `~/.hermes/kol-outreach/<campaign_id>/config.yaml`.
- `kol_identity_id` from CAL (so the draft can be linked back to the identity row).
- KOL handle, email, creator type, selling-point group, and the recommendation reason produced by discovery.

If any of these are missing, fail loudly with a chat message; do not invent values.

## Procedure

### Step 1 — Load campaign config and validate mode
1. Read `config.yaml`. Extract `mode`, `test_mode_to`, `brand_name`, `brand_sender_name`, `brand_signature` (fall back to sensible defaults only for the last two).
2. If `mode == TEST`, the draft's `to` field MUST be `test_mode_to`. Put the real KOL email in the body as `Intended recipient: <email>` so the human reviewer can verify.
3. If `mode == LIVE`, the draft's `to` is the KOL's real business email.

### Step 2 — Compose subject
Pattern (English): `Quick collab idea for @<handle> × <brand_name>`. Keep ≤ 60 chars. No emoji. No `RE:` / `FWD:`.

### Step 3 — Compose body (English)
Use this skeleton; fill the bracketed slots from card data, not from imagination:

```
Hi <first_name_or_handle>,

I'm <brand_sender_name> from <brand_name>. I came across your [specific thing — e.g. recent Reel on small-space styling] and loved how you [one concrete observation tied to the recommendation reason].

We're a <one-line brand description> brand and I think there could be a natural fit with your audience because [link to selling-point group — keep it about THEIR content, not our product specs].

Would you be open to a short chat about a potential paid Reel collaboration? Happy to share product details and creative direction if it sounds interesting.

No rush at all — appreciate your time either way.

<brand_signature>
```

Hard content rules:
- **No price, no fee range, no gifting offer, no contract language.**
- **No SKU links, no product URLs, no attachments.** Product details come in the next email (product-pitch).
- **One concrete personalization line** drawn from the recommendation reason; never generic ("love your content").
- **One CTA only**: a soft yes/no on whether they're open to a chat.
- **No tracking pixels, no UTM-laden links.** The only link allowed is the brand homepage in the signature, if `brand_signature` already contains it.

### Step 4 — Create Gmail draft
1. Call `gmail drafts.create` with `to`, `subject`, `body`. Do not set `cc`/`bcc`.
2. Apply Gmail label `kol-outreach/pending/initial` to the draft's message.
3. Capture the returned `draft_id` and `message_id`.

### Step 5 — (removed in v2) Persist state via CAL only
There is no Kanban card to update. All durable state — `draft_id`, `gmail_thread_id`, `stage='outreach'`, `sub_status='initial_drafted'` — is captured by the CAL writes in Step 6 (`cal.record_draft` + `cal.record_event`) and surfaced to the operator through the KOL Ops Console.

### Step 6 — CAL audit (mandatory, fire-and-forget)
Write the draft + a generation-rationale snapshot to the Conversation Audit Layer (`hermes-agent/plugins/kol-ops-bridge/cal.py`). These calls MUST run after Step 5; failure is logged but does not abort the skill (per CAL failure policy).

1. Resolve `kol_identity_id`: `cal.upsert_identity(handle=<handle>, primary_email=<email>, ...)` (if the orchestrator already produced one, use that id directly).
2. Register aliases: `cal.add_alias(kol_identity_id, kind='email', value=<email>)` and (after draft creation) `cal.add_alias(kol_identity_id, kind='gmail_thread_id', value=<thread_id>)`.
3. `cal.record_draft(...)` with `stage='initial'`, `sub_status='initial_drafted'`, full `subject` + `body`, and `context_snapshot` including at minimum:
   ```json
   {
     "selling_point_group": "<from card>",
     "creator_type": "<from card>",
     "recommendation_reason": "<from discovery row>",
     "brand_voice": "<from config>",
     "mode": "<TEST|LIVE>",
     "intended_recipient": "<real email>",
     "current_stage": "outreach",
     "sub_status_at_time": "initial_drafted",
     "hit_skus": [],
     "budget_per_kol": <number>,
     "absolute_floor": <number>,
     "triggered_by": "<chat|web|cron>"
   }
   ```
4. `cal.record_event(event_type='emailed_initial', stage='outreach', sub_status='initial_drafted', actor=<from caller>, payload={draft_id, message_id})`.

### Step 7 — Return
Return `{draft_id, message_id, kol_handle}` to the caller. Do not post a chat notification from this skill — the orchestrator batches notifications.

## Hard Rules
- Never call any `send` API. Drafts only.
- Never write a Chinese body; English only (the KOLs are NA).
- Never include a price, even if the user asks; refuse and escalate to chat.
- Never address more than one KOL per invocation.
- Never reuse a draft id across two KOLs; if a CAL `kol_draft_history` row with `stage='initial'` already exists for this `(kol_identity_id, campaign_id, env)`, return its `draft_id` without creating a new draft.

## Pitfalls
- Do not pull the KOL's first name from the handle if it looks ambiguous; default to the handle.
- Do not paraphrase the recommendation reason into something the KOL did not actually do; quote the concrete observation.
- Do not include "limited spots" / "exclusive" urgency language; it tanks reply rates for cold outreach.
- Do not embed images inline; reviewers in TEST MODE may forward and break attachments.
