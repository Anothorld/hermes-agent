---
name: kol-email-stage-classifier
description: Side-effect-free classifier for incoming KOL emails. Reads the latest message + thread summary + current goal_state snapshot (per lane) and outputs a structured JSON describing which goals are active per lane, which facts can be extracted (4 namespaces), what signals were detected, and any ambiguity. Does NOT write CAL, does NOT call the Bridge, does NOT draft. Always invoked by `kol-reply-dispatcher`; never invoked by humans directly.
trigger: When `kol-reply-dispatcher` (or any router that has a fresh KOL email + goal_state snapshot) needs to know "what is this KOL replying about, and what does this email tell us?". Also when an operator pastes a single KOL email into chat and asks "classify this".
tags: ["kol", "classifier", "email", "facts", "goals", "lanes"]
---

## Goal
Turn one inbound KOL email + thread context into a structured, side-effect-free
JSON judgment that downstream skills can act on. Multi-namespace fact
extraction in a single LLM pass — never split across goals/stages.

## Runtime Contract
- **No side effects.** Never write CAL, never POST to the Bridge, never draft.
- **No tool calls** that mutate state. The skill only reads its inputs and
  returns JSON.
- Output is **machine-consumed**; downstream `kol-reply-dispatcher` parses it.
  Stable JSON shape over chatty prose.
- Confidence required on every signal; ambiguity must be reported, not
  resolved.

## Inputs
1. `latest_email` — full body + headers (from / subject / date / message-id /
   in-reply-to).
2. `thread_summary` — last 3–5 messages, oldest first, condensed.
3. `current_goal_state` — `{commerce: <goal_name|null>, fulfillment:
   <goal_name|null>, publish: <goal_name|null>}` plus each goal's
   `missing_facts`. The dispatcher fetches this from
   `GET /identities/{id}/goals?campaign_id=...`.
4. `campaign_config_summary` — `paid_ceiling`, `commission_band`,
   `sku_whitelist`, `deliverable_count_per_platform`, `contract_required`,
   `audit_standards_md` excerpt. Used **only** as context, never to make a
   business decision; that's the dispatcher's job.
5. `relationship_summary` (optional) — for repeat KOLs: `last_outcome`,
   `preferred_skus`, `preferred_mode`, `default_shipping_address` flag.
6. `escalation_rules` (Phase E, optional) — parsed payload from
   `GET /policies/escalation_rules/parsed`:
   `{ "top": {...}, "rules": [ {"id": str, "signals_match": [str],
   "severity": str, "suggested_question": str,
   "required_facts_to_resume": [str]} ] }`.
   When provided, the classifier MUST run rule-matching after signal
   extraction (see Procedure step 7) and surface any deterministic match in
   `escalation_hint`. When absent (e.g. policy doc empty), behave as before.

## Output Schema
Exactly one JSON object, keys in this order, no markdown wrapping:

```json
{
  "active_goals_by_lane": {
    "commerce": "<goal_name|null>",
    "fulfillment": "<goal_name|null>",
    "publish": "<goal_name|null>",
    "meta": "<goal_name|null>"
  },
  "facts_extracted": {
    "identity": { "<identity.dotted_key>": <value>, ... },
    "offer":    { "<offer.dotted_key>":    <value>, ... },
    "fulfillment": { "<fulfillment.dotted_key>": <value>, ... },
    "approval": { "<approval.dotted_key>": <value>, ... }
  },
  "signals": [
    { "name": "<signal_id>", "confidence": 0.0-1.0, "evidence": "<short quote>" }
  ],
  "ambiguity": "<empty string if none, otherwise a one-sentence description>",
  "escalation_hint": {
    "should_consider": true|false,
    "reason": "<empty | rule pattern matched | structural ambiguity | over-cap signal>",
    "matched_rule_id": "<empty | rule_id from escalation_rules>",
    "suggested_question": "<empty | rule.suggested_question copied verbatim>",
    "required_facts_to_resume": []
  }
}
```

### Goal vocabulary
Goal names allowed in `active_goals_by_lane`:
- commerce: `cold_outreach`, `reengagement_outreach`, `interest_qualification`,
  `product_selection`, `deliverables_scope`, `compensation_negotiation`,
  `contract_signing`.
- fulfillment: `logistics`, `content_production`.
- publish: `content_review_and_golive`.
- meta: `post_collab_archival`.
Use `null` for any lane with no active goal.

### Fact namespace rules (HARD)
- Every key in `facts_extracted` MUST be dotted and prefixed by its namespace
  (`identity.`, `offer.`, `fulfillment.`, `approval.`).
- **Never** emit a key without a prefix; the Bridge will reject it with
  `FactNamespaceError` and the dispatcher run will hard-fail.
- Common keys (non-exhaustive):
  - identity: `identity.handle`, `identity.email`, `identity.preferred_language`,
    `identity.contact_role`.
  - offer: `offer.interest_signal` ∈ {confirmed, declined, needs_more_info};
    `offer.sku_locked`, `offer.color_or_variant_locked`,
    `offer.deliverable_platforms`, `offer.deliverable_count_per_platform`,
    `offer.compensation_mode` ∈ {gifted, paid, commission, hybrid},
    `offer.kol_quoted_amount`, `offer.agreed_terms`,
    `offer.contract_sent`, `offer.contract_signed`,
    `offer.contract_declined_reason`.
  - fulfillment: `fulfillment.address_collected`,
    `fulfillment.shipping_method`, `fulfillment.tracking_no`,
    `fulfillment.delivered_confirmed`, `fulfillment.brief_sent`,
    `fulfillment.draft_submitted`.
  - approval: `approval.over_budget_request`,
    `approval.contract_change_request`, `approval.review_overflow`,
    `approval.policy_overrides`, `approval.identity_drift_review`.

### Signal vocabulary (orthogonal to goals)
Common signals, append-only — emit only when evidence is in the email body:
- `interest_positive` / `interest_negative` / `interest_unclear`
- `asks_deliverables` / `asks_budget` / `asks_timeline`
- `proposes_rate` / `counter_offer` / `accepts_terms`
- `requests_oos_sku` / `requests_color_swap`
- `address_provided` / `address_questioned`
- `tracking_question` / `not_received`
- `submits_draft_url` / `accepts_revisions` / `rejects_revisions`
- `asks_to_change_contract_term` / `signs_contract` / `declines_contract`
- `out_of_office` / `auto_reply`
- `escalation_pattern_match:<rule_id>` (only if a campaign-level escalation
  rule literally pattern-matches; rule list comes from `escalation_rules`
  policy doc — Phase E)

## Procedure
1. Read `latest_email` body + `thread_summary` for context.
2. Look at `current_goal_state` to know what facts the dispatcher is hunting.
   Bias your fact extraction toward `missing_facts` — but do NOT invent values
   to fill them.
3. Per lane (commerce / fulfillment / publish / meta), assess whether the
   email implies a different active goal than `current_goal_state` says (e.g.
   the dispatcher thinks we're in `compensation_negotiation` but the email
   reverts to `product_selection` because the KOL wants to swap SKU). Emit
   the **email's view** in `active_goals_by_lane`; the dispatcher reconciles.
4. Extract facts, multi-namespace, in one pass. **Skip** any field you're not
   sure about — under-extraction is fine, hallucination is not.
5. Enumerate every signal with at least 0.6 confidence; lower-confidence
   signals go into `ambiguity` instead.
6. Set `escalation_hint.should_consider=true` if **any** of: KOL quotes >
   `paid_ceiling`, requests SKU outside whitelist, asks to change a contract
   core term, requests deliverables > campaign cap, claims package lost /
   address dispute, multi-round revision overflow.
7. **Rule matching (Phase E).** If `escalation_rules` is provided, walk
   each rule in `escalation_rules.rules` and check whether **every** entry
   in `rule.signals_match` is present in the `signals` array you just
   emitted (compare by `signal.name`; case-sensitive; no fuzzy match). On
   the first rule that matches:
   - Set `escalation_hint.should_consider = true`.
   - Set `escalation_hint.matched_rule_id = rule.id`.
   - Copy `rule.suggested_question` verbatim into
     `escalation_hint.suggested_question`.
   - Copy `rule.required_facts_to_resume` verbatim into
     `escalation_hint.required_facts_to_resume`.
   - Set `escalation_hint.reason = "rule pattern matched"`.
   If no rule matches but step 6 still triggered (over-cap / structural),
   leave `matched_rule_id` and `suggested_question` empty strings and
   `required_facts_to_resume = []`. Rule matching is deterministic — do
   **not** invent rule_ids and do **not** re-rank rules; the first match
   in declared order wins.

## Examples (few-shot mental model)

### "I'd love to collaborate! What would the deliverables and budget look like?"
```json
{
  "active_goals_by_lane": {
    "commerce": "deliverables_scope", "fulfillment": null,
    "publish": null, "meta": null
  },
  "facts_extracted": {
    "identity": {},
    "offer": { "offer.interest_signal": "confirmed" },
    "fulfillment": {},
    "approval": {}
  },
  "signals": [
    { "name": "interest_positive", "confidence": 0.95,
      "evidence": "would love to collaborate" },
    { "name": "asks_deliverables", "confidence": 0.92,
      "evidence": "what would the deliverables ... look like" },
    { "name": "asks_budget", "confidence": 0.88,
      "evidence": "and budget" }
  ],
  "ambiguity": "",
  "escalation_hint": { "should_consider": false, "reason": "",
    "matched_rule_id": "", "suggested_question": "",
    "required_facts_to_resume": [] }
}
```

### "I'm working only paid these days, $1800 for IG reel + 3 stories."
(campaign paid_ceiling = 1500)
```json
{
  "active_goals_by_lane": {
    "commerce": "compensation_negotiation", "fulfillment": null,
    "publish": null, "meta": null
  },
  "facts_extracted": {
    "identity": {},
    "offer": {
      "offer.compensation_mode": "paid",
      "offer.kol_quoted_amount": 1800
    },
    "fulfillment": {},
    "approval": {}
  },
  "signals": [
    { "name": "proposes_rate", "confidence": 0.97,
      "evidence": "$1800 for IG reel + 3 stories" },
    { "name": "interest_positive", "confidence": 0.7,
      "evidence": "implicit by quoting" }
  ],
  "ambiguity": "",
  "escalation_hint": {
    "should_consider": true,
    "reason": "rule pattern matched",
    "matched_rule_id": "paid_quote_over_ceiling",
    "suggested_question": "KOL quote exceeds paid_ceiling — approve override or counter?",
    "required_facts_to_resume": ["paid_ceiling_override"]
  }
}
```

### "Out of office until Aug 19. Will reply when back."
```json
{
  "active_goals_by_lane": {
    "commerce": null, "fulfillment": null,
    "publish": null, "meta": null
  },
  "facts_extracted": {
    "identity": {}, "offer": {}, "fulfillment": {}, "approval": {}
  },
  "signals": [
    { "name": "out_of_office", "confidence": 0.99,
      "evidence": "Out of office until Aug 19" }
  ],
  "ambiguity": "",
  "escalation_hint": { "should_consider": false, "reason": "",
    "matched_rule_id": "", "suggested_question": "",
    "required_facts_to_resume": [] }
}
```

## Failure Modes (all graded)
- Emitted any fact key without a namespace prefix → Bridge will reject.
- Hallucinated a fact value the email doesn't state.
- Made a business decision (e.g. "I think we should counter at $1500") — that
  belongs to the dispatcher / negotiator, not here.
- Drafted prose / Markdown / a reply email — forbidden; classifier output is
  pure JSON.
- Skipped fact extraction in a non-active lane when the email contains
  fulfillment/publish info — multi-namespace extraction is mandatory in one
  pass.
