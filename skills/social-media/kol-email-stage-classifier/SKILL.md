---
name: kol-email-stage-classifier
description: Side-effect-free classifier for incoming KOL emails. Reads the latest message + thread summary + current goal_state snapshot (per lane) and outputs a structured JSON describing which goals are active per lane, which facts can be extracted (5 namespaces), what signals were detected, and any ambiguity. Does NOT write CAL, does NOT call the Bridge, does NOT draft. Always invoked by `kol-reply-dispatcher`; never invoked by humans directly.
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
- Consult `references/failure-examples.md` before changing extraction rules;
  run `playground/classifier_eval/run_eval.py` after edits.
- Output is **machine-consumed**; downstream `kol-reply-dispatcher` parses it.
  Stable JSON shape over chatty prose.
- Confidence required on every signal; ambiguity must be reported, not
  resolved.

## Inputs
1. `latest_email` — full body + headers (from / subject / date / message-id /
   in-reply-to).
2. `thread_history` — JSON array of the **prior** turns in this Gmail
   thread, oldest first, excluding `latest_email`. Each entry has
   **exactly** these three keys:

   ```json
   { "from": "alice@example.com",
      "date": "Mon, 5 May 2026 14:02:11 -0700",
      "body": "<the message body, clipped>" }
   ```

   No headers, message_id, subject, snippet, or labels. Per-message
   body is clipped (~4k chars); the whole list is bounded (~24k);
   a sentinel entry with empty `from` and `body` starting with
   `... [history truncated:` marks dropped earlier turns. May be `[]`
   for the very first inbound after a cold open. **Read verbatim** —
   do not paraphrase before reasoning over it.
3. `anomaly_signals` (optional but strongly recommended) — deterministic
   pre-classification signals from the poller:

   ```json
   {
     "thread_integrity": {"status":"strict|weak|detached", "matched_by":"in_reply_to|thread_id|heuristic|none"},
     "identity_integrity": {"status":"matched|drifted|delegated|unknown", "sender_email":"...", "expected_email":"...", "reasons":[...]},
     "content_risk": "c1|c2|c3",
     "risk_controls": {"allow_autoflow": true, "gate_budget": false, "gate_contract": false, "gate_payout": false}
   }
   ```

   The classifier may refine these judgments from email semantics, but must
   keep them deterministic-friendly (no fabricated identity claims).
4. `current_goal_state` — `{commerce: <goal_name|null>, fulfillment:
   <goal_name|null>, publish: <goal_name|null>}` plus each goal's
   `missing_facts`. The dispatcher fetches this via
   `kol_bridge_tool.py get-goals --identity-id <id> --campaign-id <cid> --env TEST|LIVE`.
5. `campaign_config_summary` — `paid_ceiling`, `paid_target_budget` (if set),
   `commission_band`,
   `sku_whitelist`, `deliverable_count_per_platform`, `contract_required`,
   `audit_standards_md` excerpt. Used **only** as context, never to make a
   business decision; that's the dispatcher's job.
6. `relationship_summary` (optional) — for repeat KOLs: `last_outcome`,
   `preferred_skus`, `preferred_mode`, `default_shipping_address` flag.
7. `campaign_facts` (optional) — latest campaign-scoped fact snapshot from
   dispatch context (`offer.barter_attempted`, `offer.rate_requested`,
   `offer.proposed_amount`, …). Use when deciding escalation hints.
8. `escalation_rules` (Phase E, optional) — parsed payload from
   `kol_bridge_tool.py get-parsed-escalation-rules`:
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
    "payout":   { "<payout.dotted_key>":   <value>, ... },
    "approval": { "<approval.dotted_key>": <value>, ... }
  },
  "signals": [
    { "name": "<signal_id>", "confidence": 0.0-1.0, "evidence": "<short quote>" }
  ],
  "ambiguity": "<empty string if none, otherwise a one-sentence description>",
  "thread_integrity": {
    "status": "strict|weak|detached",
    "matched_by": "in_reply_to|thread_id|heuristic|none",
    "notes": []
  },
  "identity_integrity": {
    "status": "matched|drifted|delegated|unknown",
    "sender_email": "<email|null>",
    "expected_email": "<email|null>",
    "reasons": []
  },
  "risk_controls": {
    "allow_autoflow": true,
    "gate_budget": false,
    "gate_contract": false,
    "gate_payout": false
  },
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
- fulfillment: `logistics`, `payout_setup`, `content_production`.
- publish: `content_review_and_golive`.
- meta: `post_collab_archival`.
Use `null` for any lane with no active goal.

### Fact namespace rules (HARD)
- Every key in `facts_extracted` MUST be dotted and prefixed by its namespace
  (`identity.`, `offer.`, `fulfillment.`, `payout.`, `approval.`).
- **Never** emit a key without a prefix; the Bridge will reject it with
  `FactNamespaceError` and the dispatcher run will hard-fail.
- Common keys (non-exhaustive):
  - identity: `identity.handle`, `identity.email`, `identity.preferred_language`,
    `identity.contact_role` ∈ {kol, manager, agency, assistant}.
    When the sender self-identifies as manager/agency/rep, emit
    `identity.contact_role` accordingly; when the KOL writes directly
    from their own handle with no delegation cue, prefer `kol`.
  - offer: `offer.interest_signal` ∈ {confirmed, declined, needs_more_info};
    `offer.sku_locked`, `offer.color_or_variant_locked`,
    `offer.deliverable_platforms`, `offer.deliverable_count_per_platform`,
    `offer.compensation_mode` ∈ {gifted, paid, commission, hybrid},
    `offer.kol_paid_quote` (pure **cash supplement** on top of gifted
    product — not an all-in deal price; legacy alias `offer.kol_quoted_amount`
    is accepted by CAL but prefer `offer.kol_paid_quote`),
    `offer.agreed_terms`,
    `offer.contract_sent`, `offer.contract_signed`,
    `offer.contract_declined_reason`.
  - fulfillment: `fulfillment.address_collected`,
    `fulfillment.shipping_method`, `fulfillment.tracking_no`,
    `fulfillment.delivered_confirmed`, `fulfillment.brief_sent`,
    `fulfillment.draft_submitted`.
  - payout: `payout.payment_method_proposed` (object
    `{method:"paypal", paypal_email, account_holder_name?, country?}`
    when KOL volunteers PayPal details inline);
    `payout.alternate_method_requested` (string verbatim when KOL
    asks for wire / Stripe / Payoneer / crypto / bank instead of PayPal).
    Do NOT emit `payout.method_collected` from the classifier — that
    flag is owned by the intake skill.
  - approval: `approval.over_budget_request`,
    `approval.contract_change_request`, `approval.review_overflow`,
    `approval.policy_overrides`, `approval.identity_drift_review`,
    `approval.identity_drift_review_payment`.

### Signal vocabulary (orthogonal to goals)
Common signals, append-only — emit only when evidence is in the email body:
- `interest_positive` / `interest_negative` / `interest_unclear`
- `asks_deliverables` / `asks_budget` / `asks_timeline`
- `proposes_rate` / `counter_offer` / `accepts_terms`
- `paid_only_stance` — KOL or rep explicitly rejects barter/gifting and
  insists on paid/cash only (including after a prior barter pitch)
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
1. Read `latest_email` body for what the KOL just said. Read
   `thread_history` (oldest → newest) for what has already been said
   on both sides. The history exists so you can:
   - Avoid emitting an `ambiguity` for a question the KOL already
     answered earlier in the thread.
   - Recognize when the KOL is **repeating** a prior point (i.e. we
     ignored it last turn) — bias confidence higher for that signal.
   - Treat facts the KOL volunteered in an earlier turn (e.g. address,
     platform preference, paid-only stance) as already on the record;
     do not re-extract them as if new, but do extract any **change**
     in the latest message.
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

### Committed vs proposed (HARD — goal satisfaction)
These keys **satisfy** `goal_state` when written by the dispatcher; only emit
them when the **latest email** shows agreement, not mere questions:

| Key | Emit only when |
|-----|----------------|
| `offer.interest_signal=confirmed` | `interest_positive` or `accepts_terms` (≥0.6); never on `interest_unclear` / `asks_*` alone |
| `offer.deliverable_platforms`, `offer.deliverable_count_per_platform` | KOL states agreed platforms/counts, or `accepts_terms`; if they only **ask** what you need, omit (Bridge may rewrite to `*_proposed`) |
| `offer.usage_rights_discussed=true` | Usage rights were discussed **and** agreed or clearly accepted — not on deliverables/budget questions alone |
| `offer.agreed_terms` | `accepts_terms` — not on `proposes_rate` / `counter_offer` alone |
| `offer.sku_locked`, `offer.color_or_variant_locked`, `offer.fit_confirmed` | KOL **confirmed** a variant — not on `requests_oos_sku` / `requests_color_swap` alone |

When the KOL is asking or vague, prefer **omitting** committed keys (or
`offer.interest_signal=needs_more_info`). The Bridge also sanitizes
`email:` writes using your `signals` array.
5. Enumerate every signal with at least 0.6 confidence; lower-confidence
   signals go into `ambiguity` instead.
6. Set `escalation_hint.should_consider=true` if **any** of: requests SKU
   outside whitelist, asks to change a contract core term, requests
   deliverables > campaign cap, claims package lost / address dispute,
   multi-round revision overflow.
   **Never** escalate solely because a KOL quote exceeds `paid_ceiling` —
   the pricing engine always auto-counters down.
   **Do not** escalate on first direct-KOL `proposes_rate` / `paid_only_stance`
   when `campaign_facts.offer.barter_attempted` is absent — the negotiator
   must run barter-first instead.
7. Use `anomaly_signals` as the baseline for identity/thread/risk controls:
   - Preserve `thread_integrity.status` / `matched_by` unless the email body
     makes the baseline clearly inconsistent.
   - Preserve `identity_integrity` unless the email explicitly self-identifies
     a delegated role ("I'm <name>, creator's manager/assistant").
   - Keep `risk_controls.allow_autoflow=false` once false (never auto-upgrade
     to true in classifier output). You may only tighten gates, never loosen.
8. Promote anomaly-driven escalation hints when warranted:
   - `thread_integrity.status == "detached"` AND any of
     `gate_budget|gate_contract|gate_payout` true;
   - `identity_integrity.status == "unknown"` AND any gate true;
   - `identity_integrity.status == "delegated"` AND (`gate_contract` OR
     `gate_payout`) true — **not** for budget-only rate mail from an
     on-scope agency rep (compensation negotiation may proceed);
   - `content_risk == "c3"` (authority/ownership handoff cues).
   In these cases set `escalation_hint.should_consider=true` and put a short,
   machine-readable reason in `escalation_hint.reason` (e.g.
   `identity_drift_sensitive_topic`).
9. **Rule matching (Phase E).** If `escalation_rules` is provided, walk
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

## Minimal canonical output (format anchor)

Use this as the minimum shape contract (JSON object only):

```json
{
  "active_goals_by_lane": {
    "commerce": "interest_qualification",
    "fulfillment": null,
    "publish": null,
    "meta": null
  },
  "facts_extracted": {
    "identity": {},
    "offer": { "offer.interest_signal": "needs_more_info" },
    "fulfillment": {},
    "payout": {},
    "approval": {}
  },
  "signals": [
    { "name": "interest_unclear", "confidence": 0.86, "evidence": "tell me more" }
  ],
  "ambiguity": "",
  "thread_integrity": {
    "status": "strict",
    "matched_by": "in_reply_to",
    "notes": []
  },
  "identity_integrity": {
    "status": "matched",
    "sender_email": "alice@example.com",
    "expected_email": "alice@example.com",
    "reasons": []
  },
  "risk_controls": {
    "allow_autoflow": true,
    "gate_budget": false,
    "gate_contract": false,
    "gate_payout": false
  },
  "escalation_hint": {
    "should_consider": false,
    "reason": "",
    "matched_rule_id": "",
    "suggested_question": "",
    "required_facts_to_resume": []
  }
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
- Ignored `thread_history` and emitted an `ambiguity` for something the
  KOL plainly answered two turns ago. The history is part of context for
  exactly this reason — read it before declaring ambiguity.
