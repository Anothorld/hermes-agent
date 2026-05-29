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

## Shared Blocks (Phase 2)
- Runtime/draft guardrails:
  `references/shared/runtime-draft-guardrails.md`
- Style preamble baseline:
  `references/shared/style-and-brief-preambles.md`
- Reply envelope contract:
  `references/shared/reply-envelope-contract.md`

## Runtime Contract
- Follow shared runtime rules in
  `references/shared/runtime-draft-guardrails.md`.
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
4. `thread_history` (mandatory; may be `[]`) — JSON array of prior
   turns, oldest first, from `kol-reply-dispatcher` Step 0. Each
   entry: `{from, date, body}` only.
5. `flow_hint` — small JSON from dispatcher Step 5:
   `{lane, current_goal, next_goal_in_lane,
   missing_facts_for_current_goal, kol_signaled_next_step}`.

## Email Style Preamble (mandatory before drafting)

Follow shared style-preamble baseline in
`references/shared/style-and-brief-preambles.md`.

Call contract:
- inputs: `goal_brief = {goal: "product_selection", missing_facts: [<from goal_state>], next_action: "Propose / confirm SKU + color within whitelist"}`,
  `current_user_id = <operator id from session>`.

>>> include: kol-email-style-loader

## Conversation History Preamble (mandatory before drafting)

After the style-loader block, prepend a `[P0.3] Conversation history`
section, built verbatim from `thread_history` (oldest → newest):

```
[P0.3] Conversation history so far (oldest first; latest_email is
shown separately under [INBOUND]):

— <from> · <date>
<body>
...
```

When `thread_history` is `[]`, render
`[P0.3] Conversation history so far: (none).`

Hard rules (verbatim):

1. Do **not** re-propose a SKU the KOL has already declined in an
   earlier turn. Scan `[P0.3]` before picking proposal options.
2. Do **not** keep re-listing the same 3 whitelist items if a prior
   outbound already listed them and the KOL deflected — narrow to
   1-2 different options and add one sentence of guidance.
3. If the KOL volunteered a color / variant preference in an
   earlier turn, treat it as already noted; check it against the
   policy and confirm rather than re-asking.

## Flow Guidance Preamble (mandatory before drafting)

Immediately after `[P0.3]`, prepend `[P0.4] Flow guidance` from
`flow_hint`:

```
[P0.4] Flow guidance:
- Lane: <lane>
- Current goal in this lane: <current_goal>
- Single fact this reply should help us collect/confirm:
  <first item of missing_facts_for_current_goal>
- Next goal in this lane (if conversation naturally arrives there):
  <next_goal_in_lane>
- KOL signaled readiness to move on: <kol_signaled_next_step>
```

Hard rules (verbatim):

1. Standard lane order is a **default**, not a forced march. If the
   KOL is still picking a SKU (asks a fit question, asks for an
   option list), stay on `product_selection` regardless of
   `kol_signaled_next_step`.
2. When **all of** (a) `kol_signaled_next_step` is `true`,
   (b) the KOL did **not** raise a new product question, and
   (c) `next_goal_in_lane` is non-null — confirm the SKU and
   optionally add a one-sentence handoff toward `next_goal_in_lane`
   (e.g. "I'll come back next with the scope side"). Never start
   negotiating the next goal here.
3. Never reopen an earlier lane goal unless the KOL explicitly
   reopens it (e.g. "actually I want to drop the collab" → that
   belongs to a different lane / escalation path, not here).

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
  "body": "<reply>",
  "branch": "A_confirm | B_propose | C_escalated",
  "facts_written": {"offer": <n>}
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft` (shared:
`references/shared/reply-envelope-contract.md`).

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
- Re-proposing a SKU the KOL declined in `[P0.3]`. The history
  block exists precisely to prevent this stall loop.
- Pushing toward the next lane goal while the KOL is still
  comparing options. Confirmation and a one-sentence handoff is
  the maximum forward motion in a single reply.
