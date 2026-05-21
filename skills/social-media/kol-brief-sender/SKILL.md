---
name: kol-brief-sender
description: After delivery is confirmed (or in the gifted-no-product fast path), sends the content brief to the KOL: posting requirements, do/don't list, hashtags, mention handles, deadline, and a request to share the draft for review before posting. Reads `campaign_config.brief_template_id` + `audit_standards_md` + `extra_notes` to assemble. Writes `fulfillment.brief_sent=true` and `fulfillment.brief_sent_at`. Does not generate brief CONTENT from scratch — uses the configured template + audit standards as the spine.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.fulfillment == "content_production"` AND `fulfillment.brief_sent != true`. Also runs in the gifted-no-product fast path when `compensation.satisfied AND no logistics needed`.
tags: ["kol", "brief", "content-production", "draft-generator", "fulfillment-lane"]
---

## Goal
Send ONE well-structured brief email so the KOL knows exactly what
to produce and what to send back for review. After this turn, the
lane waits for `fulfillment.draft_submitted=true` (handled by
classifier on the next inbound).

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Brief content from config, not LLM creativity.** The body should
  be a faithful render of `campaign_config.brief_template_id` (referred
  to by id) + `audit_standards_md` + `extra_notes`. Do not invent
  content guidelines.
- **Idempotent.** If `fulfillment.brief_sent==true`, abort
  `{"skipped":"already_sent"}`.
- **Required-fact gate.** Aborts when `fulfillment.delivered_confirmed`
  is missing AND mode is not gifted-no-product. Defense-in-depth.
- **One deadline only.** Pick the earliest applicable deadline (e.g.
  contract deadline OR campaign launch date) and state it ONCE in the
  body, not multiple times.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. Optional `extra_inline_notes` (operator-supplied per-KOL notes,
   appended below the standard brief body).

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "content_production", missing_facts: ["fulfillment.brief_sent"], next_action: "Send brief / posting notes / hashtags"}`,
  `current_user_id = <operator id from session>`.
- output: prepend as the first section of the draft prompt; verbatim brief
  body from `campaign_config.brief_template_id` is content (not style) and
  is **not** subject to P1/P2 rewriting.
- failure mode: empty-doc fallbacks; never block.

>>> include: kol-email-style-loader

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `campaign_config.brief_template_id` — required, non-empty.
- `campaign_config.audit_standards_md` — required, non-empty.
- `campaign_config.deliverable_platforms`, `deliverable_count_per_platform`
  — to render the deliverable line.
- `campaign_config.extra_notes` (optional).
- `goals.content_production.status` — must be `active`.
- Latest `offer.compensation_mode` — gates skip path.
- `fulfillment.delivered_confirmed` — required unless mode is
  gifted-no-product.

If `brief_template_id` missing → abort
`{"error":"campaign_config_incomplete","missing":["brief_template_id"]}`.

### Step 2 — Compose the brief body
Sections (in this exact order):
1. **Greeting + status hook** (one line: "Now that the package is in
   your hands…" / "Excited to start on this together!").
2. **Deliverables** (one bullet per platform, count per platform).
3. **Posting guidelines** (renders `audit_standards_md` verbatim,
   trimmed to the most relevant 80% — never edit the substance).
4. **Hashtags / mentions** (from `extra_notes` if present, else say
   "we'll share final hashtags closer to launch").
5. **Deadline** (one date — pick the earliest of contract deadline /
   campaign launch / 14 days from `delivered_confirmed_at`).
6. **Draft submission ask** ("please share the draft / link via
   email a few days before publish so we can review").
7. **Sign-off**.

Insert `extra_inline_notes` (if supplied) as a single italicized
paragraph between sections 5 and 6.

Body MUST be plain text (markdown is fine but no HTML); courier email
clients break on rich content.

### Step 3 — Write facts
```
write-facts-multi --json '{
  "campaign_id":"...",
  "source":"skill:kol-brief-sender",
  "namespaces":{
    "fulfillment": {"fulfillment.brief_sent": true,
                     "fulfillment.brief_sent_at": "<iso8601>",
                     "fulfillment.brief_template_id_used": "<id>"}
  }
}'
```

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-brief-sender",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": "Brief for our collab — POVISON × @alice",
  "body": "<rendered brief>",
  "facts_written": {"fulfillment": 3}
}
```

Subject should reference brand + handle; this email is more
formal-feeling than reply turns, so a fresh subject line is
acceptable in the same thread.

## Examples

### Standard
`brief_template_id="brief-rugs-2026"`, audit standards present,
deliverables [IG×1, TT×1], deadline "2026-06-15". Skill renders the
6-section body, writes 3 fulfillment facts, returns envelope.

### Gifted no-product
`mode=gifted_no_product`, `delivered_confirmed` not required. Skill
proceeds normally provided `brief_template_id` is present.

### Idempotent
`brief_sent=true`. Aborts `{"skipped":"already_sent"}`.

## Pitfalls
- Editing `audit_standards_md` substance. The whole point of the
  config field is the standards live in policy_documents (or
  campaign_config) and stay verbatim.
- Stating the deadline twice (once explicit, once "around mid-June").
  KOLs anchor to whichever is earlier and we lose the buffer.
- Forgetting the draft-submission ask. The dispatcher needs the next
  inbound to satisfy `fulfillment.draft_submitted=true`.
- Sending two briefs (no idempotency check). The KOL gets confused
  about which brief is canonical.
