---
name: kol-product-selector
description: Drafts KOL product-pick emails from variant candidates.
trigger: Invoked by kol-reply-dispatcher when commerce lane goal is product_selection.
tags: ["kol", "product", "sku", "color-variant", "draft-generator", "commerce-lane"]
---

## Goal
Confirm or propose a product variant using **human-readable options only**
(product name + spec + link). Internal variant ids stay in facts, never in
email copy.

## Shared Blocks
- `references/shared/runtime-draft-guardrails.md`
- `references/shared/style-and-brief-preambles.md`
- `references/shared/reply-envelope-contract.md`
- `references/variant-email-template.md` (**mandatory**)

## Runtime Contract
- **No price talk** in email (negotiation is another skill).
- **No internal ids in email** — no variant ids, no merchant SKU codes.
- **Whitelist is hard** — only propose/confirm candidates whose `id` is in
  `campaign_config.sku_whitelist`.
- **Out-of-policy → manual approval** — do not auto-decline-and-counter-propose.
- **Idempotent** — if `product_selection` is `satisfied`, abort skipped.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`
2. `inbound_excerpt`
3. Optional `kol_requested_sku`, `kol_requested_color` from classifier
4. `thread_history` — `{from, date, body}` oldest first
5. `flow_hint` from dispatcher

## Email Style Preamble (mandatory)
>>> include: kol-email-style-loader

## Conversation History + Flow Guidance
Follow existing `[P0.3]` / `[P0.4]` blocks in prior SKILL versions.

## Procedure

### Step 1 — Load context
```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```

Read:
- `campaign_config.sku_whitelist` — non-empty list of **internal variant ids**
- `campaign_config.variant_candidates` — list of
  `{id, label, url, attributes, price?, ...}`
- `campaign_config.product_display_name` — KOL-visible product name
- `campaign_config.color_variant_policy` — allowed spec summary
- `relationship.preferred_skus`, `reusable_facts` as before

Build `eligible = [c for c in variant_candidates if c.id in sku_whitelist]`.

If `sku_whitelist` empty OR `eligible` empty → abort
`{"error":"campaign_config_incomplete","missing":["sku_whitelist"]}`.

### Step 2 — Branch
**Branch A — KOL requested a specific variant/spec:**
- Match request to an `eligible` candidate (by color/size/material in
  `attributes` or `label`).
- If matched → confirm using template in `variant-email-template.md`; write
  locked facts with internal `id`.
- If **not** matched (off whitelist or off policy) → **Branch C** (escalation).

**Branch B — KOL open / asking what they would sample:**
- Propose 1–3 from `eligible` (prefer `relationship.preferred_skus` ids, else
  first eligible not declined in `[P0.3]`).
- Use numbered list template; write `offer.proposed_skus` as internal ids.

**Branch C — off-policy / off-whitelist:**
```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py open-escalation \
  --env <TEST|LIVE> \
  --json '{"identity_id":<id>,"campaign_id":"<cid>","goal":"product_selection",
           "reason":"KOL requested variant outside whitelist/policy",
           "question_to_operator":"<summary>"}'
```
Return `escalation_opened`; skip draft body.

### Step 3 — Write facts
Confirm:
```json
"offer": {
  "offer.sku_locked": "<internal variant id>",
  "offer.color_or_variant_locked": "<human spec or null>",
  "offer.fit_confirmed": false
}
```
Propose:
```json
"offer": {"offer.proposed_skus": ["<id1>", "<id2>"]}
```

### Step 4 — Envelope
Standard reply envelope; `branch`: `A_confirm | B_propose | C_escalated`.

## Fragment mode (multi-goal dispatch)

When input includes `fragment_mode: true`:

- Run Steps 1–2 logic only; **do not** call `write-facts-multi` or
  `open-escalation`.
- **Do not** include greeting, sign-off, `to`, or `subject`.
- Return topic-only prose the synthesizer will merge:

```json
{
  "fragment_mode": true,
  "goal": "product_selection",
  "skill": "kol-product-selector",
  "fragment": "<1-3 sentences: product/variant only>",
  "proposed_facts": {
    "offer.proposed_skus": ["<internal variant id>"]
  },
  "branch": "B_propose"
}
```

Branch A (KOL confirmed a variant) may instead return:

```json
{
  "fragment_mode": true,
  "goal": "product_selection",
  "skill": "kol-product-selector",
  "fragment": "<confirm variant only>",
  "proposed_facts": {
    "offer.sku_locked": "<internal variant id>",
    "offer.color_or_variant_locked": "<human spec or null>",
    "offer.fit_confirmed": false
  },
  "branch": "A_confirm"
}
```

- **Branch C (off-policy):** return
  `{"fragment_mode": true, "gate": true, "reason": "...", "goal": "product_selection"}`
  — dispatcher opens escalation; no fragment.
- `proposed_facts` keys MUST be subset of
  `kol-reply-dispatcher/references/shared/fact-ownership.md` for
  `product_selection`.

## Pitfalls
- Leaking `SF8181…` or `variant 37384` into email.
- Auto-negotiating when KOL asks for unavailable spec — escalate instead.
- Locking SKU before KOL confirms a single option.
