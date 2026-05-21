---
name: kol-interest-qualifier
description: Composes ONE clarifying reply when the KOL's interest signal is ambiguous after our outreach (e.g. "thanks for reaching out" / "tell me more" / "is this paid or gifted?"). Reads dispatch-context, asks exactly ONE concrete follow-up question that maps to the missing fact (`offer.interest_signal`), writes nothing about price/products yet, and returns the draft envelope. Never sends mail directly.
trigger: Invoked by `kol-reply-dispatcher` when the classifier reports `active_goals_by_lane.commerce == "interest_qualification"` with `ambiguity != null` AND no `offer.interest_signal` confirmed/declined yet. Skip if `interest_signal` is already confirmed (caller mis-routed).
tags: ["kol", "interest", "clarify", "draft-generator", "commerce-lane"]
---

## Goal
Move `interest_qualification` from ambiguous to actionable in ONE
short reply. Either:
- KOL responds with explicit confirmation/decline → next dispatcher
  pass advances commerce lane, or
- KOL responds with a concrete blocker (paid only, off-brand, busy) →
  next dispatcher pass routes to compensation-negotiator or archive.

This skill is **side-effect-light**: it writes a single fact
recording that we asked, but does NOT pre-commit interest as confirmed.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **One clarifying question max.** Never bundle interest +
  product + deliverables + price into one paragraph; that's exactly
  the trap this skill exists to avoid.
- **No price talk, no SKU talk, no deliverable counts.** Those are
  later goals.
- **Idempotent on already-confirmed.** If
  `goals.interest_qualification.status == "satisfied"`, abort
  `{"skipped":"already_qualified"}`.

## Inputs
1. `identity_id` (mandatory).
2. `campaign_id` (mandatory).
3. `env` (`TEST|LIVE`).
4. `thread_id` (mandatory — this is a reply, not a fresh thread).
5. `inbound_excerpt` (1-3 sentence quote of KOL's ambiguous reply,
   for grounding the question).

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "interest_qualification", missing_facts: ["offer.interest_signal"], next_action: "Disambiguate KOL's reply with one focused question"}`,
  `current_user_id = <operator id from session>`.
- output: prepend as the first section of the draft prompt.
- failure mode: empty-doc fallbacks; never block.

>>> include: kol-email-style-loader

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Verify `goals.interest_qualification.status == "active"`. Else abort.

### Step 2 — Pick THE question
Choose the single most informative question based on `inbound_excerpt`:

| KOL signal | Question to ask |
|---|---|
| "tell me more" | "Quick context: it's a `<gifted|paid mix>` collab around `<one product line>`. Would that work for you?" |
| "is this paid?" | "It's primarily product-gifting, with some flexibility on the comms side. Would you be open to that?" |
| "what brand is this?" | One-line brand pitch + "Would that be a fit for your audience?" |
| "I'm busy / next quarter" | "Totally understand — would `<month X>` work better, or shall we close this out?" |
| Generic positive ("love it!") | "Glad to hear! Just to confirm — are you up for moving forward?" |

If `inbound_excerpt` doesn't match any, default to the generic
"Just confirming — are you up for moving forward?".

### Step 3 — Write the "asked" fact
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-interest-qualifier",
            "namespaces":{
              "offer": {"offer.interest_clarify_asked": true,
                         "offer.interest_clarify_question": "<the question text>"}
            }}'
```

We do NOT set `offer.interest_signal` here — only the KOL's actual
reply (via classifier on the next inbound) sets that.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-interest-qualifier",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "<thread_id>",
  "subject": null,
  "body": "<the reply>",
  "facts_written": {"offer": 2}
}
```
`subject: null` because this is a reply in an existing thread — the
caller appends to the existing subject with "Re:" automatically.

## Examples

### Success — paid question
Inbound: "Hi, thanks! Is this paid or gifted?"
Reply: "Hi @alice — primarily product-gifting, with some flexibility
on the comms side. Would you be open to that?"
Facts: `offer.interest_clarify_asked=true` + `..._question=<text>`.

### Failure — already qualified
`goals.interest_qualification.status="satisfied"`. Skill aborts
`{"skipped":"already_qualified"}` so the dispatcher routes to the
next active goal instead.

## Pitfalls
- Two questions in one reply → KOL only answers the easier one and
  the lane stalls. Stick to ONE.
- Mentioning price / SKU / deliverable counts contaminates downstream
  skill scope and removes their negotiation surface.
- Pre-committing `interest_signal=confirmed` on the basis of "love
  it!" alone — must wait for the KOL's actual confirmation reply.
