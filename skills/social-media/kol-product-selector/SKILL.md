---
name: kol-product-selector
description: Composes a product-pick reply when the KOL has confirmed interest and now needs to choose / change SKU or color. Reads dispatch-context (including `campaign_config.sku_whitelist` and `color_variant_policy`), proposes 1-3 SKU options strictly within the whitelist, handles "can I have X color?" requests against the color policy, writes `offer.proposed_skus` / `offer.sku_locked` / `offer.color_or_variant_locked` as appropriate, and returns the draft envelope.
trigger: Invoked by `kol-reply-dispatcher` when the classifier reports `active_goals_by_lane.commerce == "product_selection"`. Typical inbound: "what would I be sampling?" / "do you have X in red?" / "I'd love the Y model".
tags: ["kol", "product", "sku", "color-variant", "draft-generator", "commerce-lane"]
---

## Goal
Land on a product (SKU + color/variant if applicable) that is BOTH in
`campaign_config.sku_whitelist` AND consistent with
`color_variant_policy`. Either:
- Propose 1-3 whitelist options to the KOL, OR
- Confirm a KOL-requested SKU/color when it's allowed, OR
- Politely decline + counter-propose when KOL asks for an
  off-whitelist or off-policy item, OR
- Open an escalation when KOL insists on off-policy (defense:
  classifier should have already flagged this; we re-check).

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **No price talk.** Pricing is `kol-compensation-negotiator`'s
  domain; this skill confirms what they're getting, not what it
  costs.
- **Whitelist is hard.** Never propose or confirm a SKU not in
  `campaign_config.sku_whitelist`.
- **`color_variant_policy` is hard for KOL-initiated changes.** If
  the policy disallows a requested variant, decline politely and
  counter-propose; do not silently substitute.
- **Idempotent.** If `goals.product_selection.status == "satisfied"`,
  abort `{"skipped":"already_locked"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `inbound_excerpt`.
3. Optional `kol_requested_sku`, `kol_requested_color` (extracted by
   classifier into `facts_extracted.offer`).

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "product_selection", missing_facts: [<from goal_state>], next_action: "Propose / confirm SKU + color within whitelist"}`,
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
Verify `goals.product_selection.status == "active"`. Read:
- `campaign_config.sku_whitelist` — required, non-empty list.
- `campaign_config.color_variant_policy` — free-text or null;
  treat null as "no variant changes allowed".
- `relationship.preferred_skus` (from prior collabs, if any).
- `reusable_facts['offer.proposed_skus']` (if reengagement skill
  pre-seeded a proposal).

If `sku_whitelist` is empty, abort with
`{"error":"campaign_config_incomplete","missing":["sku_whitelist"]}`.

### Step 2 — Decide the response shape
Three branches:

**Branch A — KOL requested a specific SKU:**
- If `kol_requested_sku ∈ sku_whitelist`: confirm it. If
  `kol_requested_color` present, validate against
  `color_variant_policy`. If color allowed → confirm both. If color
  not allowed → confirm SKU but counter-propose policy-allowed colors.
- If `kol_requested_sku ∉ sku_whitelist`: decline politely, counter-
  propose 1-2 closest whitelist options with one sentence on why
  ("we're focused on `<line>` for this drop").

**Branch B — KOL is open ("anything is fine"):**
- Propose top 1-3 from `sku_whitelist`, prioritized by:
  1. Items in `relationship.preferred_skus` (repeat KOL).
  2. Items in `offer.proposed_skus` (reengagement seed).
  3. Otherwise first 3 of `sku_whitelist`.
- One concise line per item. Ask KOL to pick.

**Branch C — Off-policy variant insistence:**
- If `inbound_excerpt` makes it clear KOL refuses any whitelist option,
  do NOT keep negotiating — instead trigger escalation:
  ```
  kol_bridge_tool.py open-escalation --env <TEST|LIVE> \
    --json '{"identity_id":42,"campaign_id":"TS8319",
              "goal":"product_selection",
              "reason":"KOL insists on off-whitelist SKU <X>",
              "operator_note":"<inbound_excerpt>"}'
  ```
  Return `{"escalation_opened": true, "id": ...}` and skip Step 3+4.

### Step 3 — Write outbound facts
For Branch A confirm:
```
write-facts-multi --json '{
  "campaign_id":"...",
  "source":"skill:kol-product-selector",
  "namespaces":{
    "offer":{"offer.sku_locked":"<sku>",
             "offer.color_or_variant_locked":"<color or null>",
             "offer.fit_confirmed": false}
  }
}'
```
For Branch B propose:
```
"offer":{"offer.proposed_skus":["sku-a","sku-b","sku-c"]}
```
Do NOT set `sku_locked` until KOL replies confirming a single SKU.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-product-selector",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": null,
  "body": "<reply>",
  "branch": "A_confirm | B_propose | C_escalated",
  "facts_written": {"offer": <n>}
}
```

## Examples

### Branch A success
Inbound: "Can I sample the rug-04 in beige?"
`sku_whitelist=["rug-04","rug-05"]`,
`color_variant_policy="beige and grey allowed"`. Confirm rug-04 +
beige; write `offer.sku_locked=rug-04` + `offer.color_or_variant_locked=beige`.

### Branch B propose
Inbound: "Sure, what would I be sampling?"
Propose top 3 whitelist items. Write `offer.proposed_skus=[...]`.

### Branch C escalate
Inbound: "I'd only do this with the limited gold edition."
`gold` not in policy. Open escalation; return `escalation_opened`.

## Pitfalls
- Proposing more than 3 SKUs → KOL chooses none and stalls.
- Setting `offer.sku_locked` based on a proposal (not a KOL
  confirmation). Lock only after KOL says "yes, I'll take rug-04".
- Mentioning price ("rug-04 is $200 retail"). The product line is
  about fit; the price line is about negotiation.
- Forgetting `color_or_variant_locked` — downstream brief-sender
  needs it.
