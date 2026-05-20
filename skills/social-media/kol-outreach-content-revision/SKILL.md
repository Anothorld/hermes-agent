---
name: kol-outreach-content-revision
description: Draft (never send) an English revision-request email to one KOL after the operator has reviewed a submitted video in the Web console and clicked "revise". Quotes the operator's revision notes verbatim while staying polite and concrete. Returns the Gmail draft id; no auto-send.
trigger: When the Web console pushes a content_revision_requested event (operator clicked "revise" on a submitted video) and a follow-up email draft must be produced for the same Gmail thread.
tags: ["kol", "outreach", "content", "revision", "email", "draft"]
---

## Goal
Create exactly one Gmail draft on the existing KOL thread, asking the KOL to revise their submitted video according to the operator's notes. The email must be respectful, concrete, and reference the specific submission version. No price changes, no contract changes, no new SKU proposals.

## Inputs (from caller)
- `campaign_id`, `kol_handle`, `kol_identity_id`, `gmail_thread_id` (CAL's `card_id` column is legacy — pass NULL)
- `submission_version` (e.g. `2` for v2 → request v3)
- `revision_notes` (free-text, operator-written, MUST be quoted verbatim somewhere in the body)
- Path to `~/.hermes/kol-outreach/<campaign_id>/config.yaml` for brand voice / sender / signature / test inbox / mode
- `triggered_by` (always `web` for this skill)
- `env` (`TEST` | `LIVE`)

Fail loudly if any required input is missing; refuse if `revision_notes` is empty.

## Procedure

### Step 1 — Load campaign config and apply TEST MODE
1. Read `config.yaml`; extract `mode`, `test_mode_to`, `brand_sender_name`, `brand_signature`.
2. If `mode == TEST`, set draft `to = test_mode_to` and prepend `Intended recipient: <kol_email>` as the first line of the body.

### Step 2 — Compose subject
Reply on the existing thread (`In-Reply-To` / `References` headers via `threadId`). Subject should follow Gmail's automatic `Re: ...` of the original initial outreach. Do not invent a new subject.

### Step 3 — Compose body (English, ≤ 150 words)
Skeleton:
```
Hi <first_name_or_handle>,

Thanks so much for sending over v<submission_version> — really appreciate the quick turnaround.

We took a look on our side and there are a few things we'd love to tweak before we go live:

<revision_notes — quoted verbatim, as bullet points if the operator wrote bullets>

No need to rush; please send the updated cut whenever it's ready and we'll review again on our end.

<brand_signature>
```

Hard content rules:
- The `revision_notes` block is **quoted verbatim** — do NOT paraphrase, summarize, or "soften" the operator's wording. If the operator wrote `Please cut the music in the intro.`, the email must contain that sentence (formatting only — line breaks / bullets — may be normalized).
- Refer to the version number explicitly (`v<submission_version>`) so the KOL knows which take you mean.
- No price talk, no contract talk, no new product offer. If the operator's notes touch any of those, escalate and DO NOT draft.
- One CTA only: send the updated cut whenever ready.

### Step 4 — Create Gmail draft (reply on thread)
1. Call `gmail drafts.create` with `threadId = <gmail_thread_id>`, `to`, `subject`, `body`.
2. Apply label `kol-outreach/pending/content_revision` to the draft.
3. Capture `draft_id`, `message_id`.

### Step 5 — Write CAL: draft + event
1. `cal.record_draft` with:
   - `stage: content_revision`
   - `sub_status: requested_v<submission_version+1>`
   - `draft_id`, `gmail_message_id`, `gmail_thread_id`, `subject`, `body`
   - `context_snapshot`:
     ```json
     {
       "selling_point_group": "<from card>",
       "current_stage": "content_delivery",
       "sub_status_at_time": "revision_requested_v<submission_version>",
       "previous_video_url": "<from card content.submissions[version=v_n].url>",
       "revision_notes": "<operator notes>",
       "operator": "<actor from event>",
       "triggered_by": "web"
     }
     ```
   - `actor: web:operator`, `triggered_by: web`
2. `cal.record_event` with:
   - `event_type: emailed_content_revision`
   - `stage: content_delivery`, `sub_status: revision_drafted_v<submission_version+1>`
   - `payload: {draft_id, target_version: submission_version + 1}`

### Step 6 — Return
No Kanban card update. The Step 5 CAL writes (`cal.record_draft` with `stage='content_revision'` + `cal.record_event` with `event_type='emailed_content_revision'`) are the single source of truth; the KOL Ops Console renders the new draft from CAL.

Return `{draft_id, message_id, target_version}`. The orchestrator/notifier batches the human notification.

## Hard Rules
- Never call any `send` API. Drafts only — operator reviews in Gmail.
- Never change subject (always reply on thread).
- Never modify the operator's `revision_notes` content; only formatting (line breaks, bullets) may be normalized.
- Never propose price changes or new product variants in this skill — those go through negotiation / product-pitch skills.
- Never produce more than one draft per invocation. Revisions are inherently sequential.

## Pitfalls
- Do not draft when `revision_notes` is empty — the operator forgot to fill the form; bounce back.
- Do not skip the `target_version = submission_version + 1` math; the Web timeline relies on this incrementing.
- Do not lose the thread: always set `threadId`. Without it Gmail creates a fresh conversation and the dispatcher's threadId matching will lose the link.
