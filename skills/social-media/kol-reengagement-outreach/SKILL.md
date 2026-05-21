---
name: kol-reengagement-outreach
description: Generates the FIRST outreach email for a REPEAT KOL ("repeat_kol" path). Reads campaign config + identity facts + relationship history (last_outcome, preferred_skus, preferred_mode, default_shipping_address) via the Bridge dispatch-context, composes a warm "back again" opening that references the prior collab and proposes the most likely next collab shape, writes outbound facts (`offer.outreach_sent=true`, `offer.outreach_path=reengagement`) via the Bridge, and returns the draft envelope as JSON for the caller to persist. Never sends mail directly.
trigger: Invoked by `kol-discovery-to-outreach-router` for candidates assigned `identity.outreach_path=reengagement` (i.e. `relationship_status=repeat_kol`, NOT `repeat_kol_needs_review` — those open an escalation instead). Or on demand when the user says "draft a re-engagement email to <handle>".
tags: ["kol", "outreach", "reengagement", "repeat-collab", "draft-generator", "commerce-lane"]
---

## Goal
Produce one warm re-engagement email that
1. acknowledges the prior collab,
2. proposes a concrete next collab consistent with prior mode +
   preferred SKUs,
3. asks ONE confirmation question (default: "shipping info same as
   before?" if `default_shipping_address` is on file),
and atomically records the outreach on the Bridge. No reply handling,
no Gmail send.

## Runtime Contract
- Profile: `outreach-operator`.
- Bridge is the only CAL writer. `--env <TEST|LIVE>` mandatory.
- **Refuses risky repeats.** If
  `relationship.last_outcome ∈ {disputed, content_failed, aborted}`,
  abort and return
  `{"skipped":"needs_review","delegate_to":"escalation"}`; the router
  is supposed to open an escalation in that case, not call this
  skill. Defense-in-depth.
- **Single-shot per (identity, campaign).** If
  `offer.outreach_sent=true` already, abort with `{"skipped":"already_sent"}`.
- **No price talk in the opening.** May reference prior mode
  ("happy to do another gifted collab" / "if commission works again
  for you") but does NOT quote numbers.
- **Reuses identity-level facts.** Default shipping address /
  preferred mode / preferred SKUs are loaded from
  `reusable_facts` and used as soft defaults the KOL can confirm in
  one reply.

## Inputs
1. `identity_id` (mandatory).
2. `campaign_id` (mandatory).
3. `env` (`TEST` or `LIVE`, mandatory).

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```

Required signals:
- `goals.outreach.status == "active"` (else abort `already_sent`).
- `relationship.total_collabs >= 1` (else abort
  `{"skipped":"not_a_repeat_kol","delegate_to":"kol-cold-outreach"}`).
- `relationship.last_outcome ∉ {disputed, content_failed, aborted}` —
  these MUST go through escalation; do not draft for them.
- `reusable_facts` keys to use:
  - `identity.default_shipping_address` (decides the confirm question).
  - `identity.preferred_skus` (mention in proposal if present).
  - `identity.preferred_mode` (gifted / paid / commission / hybrid).

### Step 2 — Compose the email
Constraints:
- Subject: warm, references continuity. Example:
  "Round 2? <Brand> × @<handle>" or
  "Back with another POVISON drop for you".
- Body: 3–5 short paragraphs.
  1. Greeting + appreciation reference to the prior collab (one line —
     do not over-explain). If `last_outcome` is `success` or
     `success_with_revisions`, say so warmly.
  2. Concrete proposal: cite up to 2 items from `preferred_skus`, or
     "another piece similar to what worked last time" if absent.
     Match prior `preferred_mode` ("happy to do gifted again" /
     "if commission works for you again"); never escalate the mode
     unsolicited.
  3. ONE confirmation question — preferably:
     "Shipping info same as before — `<masked address one-liner>`?"
     when `default_shipping_address` is present. Otherwise:
     "Would you be up for it? Happy to share more if so."
     **Mask** the address in the email: show city/country only,
     never the full street; the address is just for the KOL to
     confirm/correct, not for us to broadcast it.
  4. Sign-off.
- Do NOT ask for new deliverables/platforms here — defer to
  `kol-deliverables-clarifier` after the reply.

### Step 3 — Write outbound facts (single call)
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-reengagement-outreach",
            "namespaces":{
              "offer":    {"offer.outreach_sent": true,
                            "offer.outreach_path": "reengagement",
                            "offer.proposed_mode": "<gifted|paid|commission|hybrid>",
                            "offer.proposed_skus": ["sku-a","sku-b"]},
              "identity": {"identity.last_outreach_at": "<iso8601>"}
            }}'
```

`offer.proposed_mode` and `offer.proposed_skus` are intentionally
captured so the downstream
`kol-product-selector` / `kol-compensation-negotiator` skills can read
them without re-deriving from prose. Omit `offer.proposed_skus` if
no preferred SKUs were on file.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-reengagement-outreach",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "subject": "Round 2? POVISON × @alice",
  "body": "Hey Alice, ...",
  "to": "<resolved from identity.primary_email>",
  "thread_id": null,
  "address_confirm_asked": true,
  "facts_written": {"offer": 4, "identity": 1}
}
```

`thread_id: null` because we always start a fresh thread for a fresh
campaign — the prior collab thread is a different campaign.

## Examples

### Success — same mode, same SKUs, same address
KOL `@alice`, `total_collabs=1`, `last_outcome=success`,
`preferred_mode=gifted`, `preferred_skus=["povi-rug-04"]`,
`default_shipping_address` present. Step 2 composes a warm 4-para
email proposing "another piece similar to the rug from last time" +
ONE address-confirmation question masked to "shipping to your London
address?". Step 3 writes 4 offer facts + 1 identity fact in one call.

### Failure — risky repeat
`last_outcome="content_failed"`. Skill aborts with
`{"skipped":"needs_review","delegate_to":"escalation"}` — the router
should have caught this upstream and opened an escalation instead.

### Failure — actually a new prospect
`total_collabs=0`. Skill aborts with
`{"skipped":"not_a_repeat_kol","delegate_to":"kol-cold-outreach"}`.

## Pitfalls
- Never quote prices, commission percentages, or deliverable counts
  in the reengagement email. Continuity ≠ blanket-renewal of terms.
- Do not output the full default shipping address verbatim — mask to
  city/country in the email body. The structured address stays in
  `kol_identity` for downstream skills to read.
- Do not silently bump the mode (e.g. last collab gifted, this email
  proposing paid). Mode escalations belong to the
  `kol-compensation-negotiator` after the reply.
- Do not redraft if `outreach_sent=true`; abort with `already_sent`.
- Do not call `cal.py` / direct SQL / `execute_code`.
