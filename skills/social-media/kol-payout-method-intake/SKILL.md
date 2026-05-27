---
name: kol-payout-method-intake
description: Collects the KOL's PayPal payout details after the contract is signed (or skipped) when compensation is paid/commission/hybrid. First tries to reuse `identity.default_payment_method` with a single one-line confirmation question (masked email); only on KOL correction or absence of default does it ask for the structured PayPal fields. Writes `payout.method_collected=true` (with structured `payout.payment_method` snapshot) when KOL confirms; writes `approval.identity_drift_review_payment` when KOL provides a NEW PayPal email that conflicts with `identity.default_payment_method`. PayPal is the default channel — non-PayPal requests (wire / Stripe / Payoneer) open an escalation rather than auto-accept.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.fulfillment == "payout_setup"` AND `payout.method_collected != true`. Skipped automatically when `offer.compensation_mode ∈ {gifted, gifted_no_product}` (no payout owed).
tags: ["kol", "payout", "paypal", "draft-generator", "fulfillment-lane"]
---

## Goal
Capture a usable PayPal account for the KOL with the fewest possible
questions:
- KOL has prior PayPal on file → ONE confirmation: "still paying to
  `<masked>`?"
- No prior PayPal → request `paypal_email`, `account_holder_name`,
  and optional `country`.
- KOL replies with a different PayPal email → `method_collected=true`
  with the new value AND open `approval.identity_drift_review_payment`
  for the archival writer to handle later (do NOT silently overwrite
  identity default).
- KOL asks for a non-PayPal method (wire / Stripe / Payoneer / crypto)
  → open escalation; do not commit. PayPal is the default channel.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Mask any prior PayPal email in the email body** as
  `<first char>***@<domain>` (e.g. `j***@gmail.com`). Never echo the
  full local-part.
- **Never silently overwrite `kol_identity.default_payment_method`.**
  That's archival-writer's job after the campaign closes, gated by
  `approval.identity_drift_review_payment.decision == "approved"`.
- **Skipped when no payout owed.** If
  `offer.compensation_mode ∈ {gifted, gifted_no_product}` or
  `goals.payout_setup.status == "skipped"`, abort
  `{"skipped":"no_payout_required"}`.
- **Hard gate on contract.** If `campaign_config.contract_required == true`
  and `offer.contract_signed != true`, abort
  `{"skipped":"contract_not_signed"}`. Defense-in-depth — the router
  should have gated upstream.
- **Idempotent.** If `payout.method_collected == true`, abort
  `{"skipped":"already_collected"}`.
- **PayPal is the default.** Non-PayPal requests open an escalation
  (`payout_method_alternate_requested`); the skill does NOT write
  alternate-method payout facts on its own.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `mode`: `ask | handle_response`.
3. `inbound_excerpt` (only for `handle_response`).
4. Classifier-extracted `payout.payment_method_proposed` when KOL
   provided one inline.

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "payout_setup", missing_facts: ["payout.method_collected"], next_action: "<reuse default PayPal / collect new PayPal details>"}`,
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
Read:
- `reusable_facts['identity.default_payment_method']` — JSON object,
  expected shape `{method:"paypal", paypal_email, account_holder_name,
  country?, captured_at}`. May be `null` for first-time paid KOLs.
- `goals.payout_setup.status` (must be `active`).
- Latest `offer.compensation_mode` and `offer.contract_signed`.
- `campaign_config.contract_required`.

### Step 2 — Branch on `mode`

**Branch ASK — first turn:**
- If `default_payment_method.method == "paypal"` and
  `default_payment_method.paypal_email` is present:
  > "One last setup item — should I send the payout to your PayPal
  > at `<masked email>`? Reply 'yes' or send the address you'd like
  > us to use."
- Else (no PayPal on file, or stored method is non-PayPal):
  > "To set up your payout: could you share your PayPal email and
  > the name on the account? PayPal is how we pay all our creators."
- Write nothing — we haven't received anything yet.

**Branch HANDLE_RESPONSE:**

| KOL signal | Action |
|---|---|
| "yes, same as before" | write `payout.method_collected=true` + `payout.payment_method=<copy of identity default>` (snapshot, so future identity changes don't retro-affect this campaign) |
| KOL provided new PayPal email (classifier passes `payout.payment_method_proposed` with `method:"paypal"` + `paypal_email` + `account_holder_name`) | write `payout.method_collected=true` + `payout.payment_method=<new>` AND if `paypal_email` differs from `identity.default_payment_method.paypal_email`, write `approval.identity_drift_review_payment={"old": <masked>, "new": <masked>, "decision":"pending"}` |
| KOL gave partial info ("just send to my Gmail") | write nothing; draft a follow-up requesting the missing fields (paypal email AND account holder name) |
| KOL requests a non-PayPal method (wire / Stripe / Payoneer / crypto / bank) | open escalation `goal=payout_setup reason="payout_method_alternate_requested" detail=<verbatim excerpt>`; do NOT write any `payout.*` facts |
| KOL refuses to share payment info | open escalation `goal=payout_setup reason="payout_collection_refused" detail=<excerpt>` |

### Step 3 — Compose the email (if drafting)
ASK branch and partial-info follow-up draft a body. Confirmation
acknowledgement may draft a one-liner ("Great — I'll get the payout
queued once deliverables are live.") OR return `body: null` to let
the dispatcher decide whether to acknowledge.

Masking rule for PayPal email: keep first character of the local-part,
replace the rest with `***`, keep the domain. Examples:
- `becki.owens@gmail.com` → `b***@gmail.com`
- `j@protonmail.com` → `j***@protonmail.com`
- Never echo the full local-part in the body.

### Step 4 — Write facts (per Step 2 table)
Atomic `write-facts-multi` calls; never partial.

Confirmed-default example:
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-payout-method-intake",
            "namespaces":{
              "payout": {"payout.method_collected": true,
                          "payout.payment_method": <identity snapshot>}
            }}'
```

New-PayPal-with-drift example: include both `payout.*` and
`approval.identity_drift_review_payment` in the same call so the
write is atomic.

### Step 5 — Return draft envelope
```json
{
  "skill": "kol-payout-method-intake",
  "mode": "ask | handle_response",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "body": "<reply or null>",
  "branch_action": "asked | confirmed_default | new_paypal_with_drift | partial_followup | escalated_alternate_method | escalated_refused",
  "facts_written": {"payout": <n>, "approval": <n>},
  "escalation_opened": false
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft`.

## Examples

### Confirmed default
Repeat KOL `@alice`, `identity.default_payment_method =
{method:"paypal", paypal_email:"alice@example.com", account_holder_name:"Alice Chen"}`.
Branch ASK email asks "send the payout to your PayPal at
`a***@example.com`?". KOL replies "yes". Branch HANDLE_RESPONSE
writes `payout.method_collected=true` + a snapshot of the identity
default into `payout.payment_method`.

### New PayPal email with drift
Default was `alice@example.com`. KOL replies "actually please use
`alice.chen@business.com` — switched accounts". Skill writes
`payout.method_collected=true` + `payout.payment_method={method:"paypal",
paypal_email:"alice.chen@business.com", account_holder_name:"Alice Chen"}`
AND `approval.identity_drift_review_payment={"old":"a***@example.com",
"new":"a***@business.com","decision":"pending"}`. ApprovalsPage
surfaces it; archival-writer will promote on approval.

### First-time paid KOL
`identity.default_payment_method == null`. Branch ASK requests
`paypal_email` + `account_holder_name`. KOL provides both. Skill
writes `payout.method_collected=true` + new `payout.payment_method`
without a drift review (no prior value to drift from).

### Alternate-method request
KOL replies "could you do a Wise transfer instead?". Skill opens
escalation `payout_method_alternate_requested` with the verbatim
excerpt. No `payout.*` facts written. Operator decides whether to
support Wise for this collab.

### Skipped — gifted
`offer.compensation_mode == "gifted"`. Skill aborts
`{"skipped":"no_payout_required"}`.

### Skipped — contract not signed
`campaign_config.contract_required == true` and
`offer.contract_signed != true`. Skill aborts
`{"skipped":"contract_not_signed"}`.

## Pitfalls
- Echoing the full PayPal email in the body. Mask the local-part:
  `b***@gmail.com`, never `becki.owens@gmail.com`.
- Silently overwriting `kol_identity.default_payment_method` when the
  KOL provides a new email. Always file a drift review via
  `approval.identity_drift_review_payment` for the archival writer.
- Asking for `paypal_email` + `account_holder_name` again when an
  identity default exists — that defeats the one-line UX.
- Marking `payout.method_collected=true` from a partial reply (e.g.
  email without holder name); let the follow-up close the loop first.
- Auto-accepting a non-PayPal method (wire, Stripe, crypto, bank).
  PayPal is the default channel; alternates require explicit operator
  approval via escalation.
- Running for gifted-only KOLs. The skill must short-circuit when
  `offer.compensation_mode ∈ {gifted, gifted_no_product}`.
- Running before the contract is signed when `contract_required=true`.
  The router gates upstream, but the skill must defend in depth.
- Writing `approval.identity_drift_review` (the shipping key) instead
  of `approval.identity_drift_review_payment`. Archival-writer hard-codes
  the former to address promotion; mixing them up corrupts the address
  drift review.
