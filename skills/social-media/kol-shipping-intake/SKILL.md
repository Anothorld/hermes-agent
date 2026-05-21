---
name: kol-shipping-intake
description: Collects the shipping address after contract is signed (or skipped). First tries to reuse `identity.default_shipping_address` with a single one-line confirmation question; only on KOL correction or absence of default does it ask for the full address fields. Writes `fulfillment.address_collected=true` (with structured `fulfillment.shipping_address`) when KOL confirms; writes `approval.identity_drift_review` when KOL provides a NEW address that conflicts with `identity.default_shipping_address`.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.fulfillment == "logistics"` AND `fulfillment.address_collected != true`. Skipped automatically when `compensation.mode == "commission_no_product"` (no shipment needed).
tags: ["kol", "shipping", "address", "draft-generator", "fulfillment-lane"]
---

## Goal
Get a usable shipping address with the fewest possible questions:
- KOL has prior address → ONE confirmation: "shipping to your `<masked>`?"
- No prior address → request the structured fields explicitly.
- KOL replies with a different address → address_collected=true with
  the new value AND open `approval.identity_drift_review` for the
  archival writer to handle later (do NOT silently overwrite identity
  default).

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Mask any prior address in the email body** to city/country.
  Never echo the full street.
- **Never silently overwrite `kol_identity.default_shipping_address`.**
  That's archival-writer's job after the campaign closes.
- **Skipped when no product.** If the latest commit shows
  `offer.compensation_mode == "commission_no_product"` or
  `goals.logistics.status == "skipped"`, abort
  `{"skipped":"no_shipment_required"}`.
- **Idempotent.** If `fulfillment.address_collected == true`, abort
  `{"skipped":"already_collected"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `mode`: `ask | handle_response`.
3. `inbound_excerpt` (only for `handle_response`).
4. Classifier-extracted `fulfillment.shipping_address_proposed` when
   KOL provided one.

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `reusable_facts['identity.default_shipping_address']` (object with
  street/city/region/country/postal_code).
- `goals.logistics.status` (must be `active`).
- Latest `offer.compensation_mode`.

### Step 2 — Branch on `mode`

**Branch ASK — first turn:**
- If default address present:
  > "Quick one before I trigger the package — shipping to your
  > `<city>, <country>` address as before? Reply 'yes' or send the
  > new address if it's changed."
- Else:
  > "Could you share your shipping address? I'll need
  > `name / street / city / region / postal code / country` and a
  > phone number for the courier."
- Write nothing — we haven't received anything yet.

**Branch HANDLE_RESPONSE:**

| KOL signal | Action |
|---|---|
| "yes, same as before" | write `fulfillment.address_collected=true` + `fulfillment.shipping_address=<copy of identity default>` (snapshot, so future identity changes don't retro-affect this campaign) |
| KOL provided full new address | classifier passes `fulfillment.shipping_address_proposed`; if structurally complete (street/city/postal/country present) → write `address_collected=true` + `shipping_address=<new>` AND if it differs from `identity.default_shipping_address`, write `approval.identity_drift_review={"old": <masked>, "new": <masked>, "decision":"pending"}` |
| KOL partial address ("Just send to my LA studio") | write nothing; draft a follow-up requesting the missing fields |
| KOL refuses or says "send digitally" | open escalation `goal=logistics reason="KOL refused physical address: <excerpt>"` |

### Step 3 — Compose the email (if drafting)
ASK branch and partial-address follow-up draft a body. Confirmation
acknowledgement may draft a one-liner ("Great — package on the way,
I'll share tracking soon.") OR return `body: null` to let the
dispatcher decide whether to acknowledge.

### Step 4 — Write facts (per Step 2 table)
Atomic `write-facts-multi` calls; never partial.

### Step 5 — Return draft envelope
```json
{
  "skill": "kol-shipping-intake",
  "mode": "ask | handle_response",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": null,
  "body": "<reply or null>",
  "branch_action": "asked | confirmed_default | new_address_with_drift | partial_followup | escalated",
  "facts_written": {"fulfillment": <n>, "approval": <n>},
  "escalation_opened": false
}
```

## Examples

### Confirmed default
KOL `@alice` had `default_shipping_address={city:'London',country:'UK',...}`.
Branch ASK email asks "shipping to your London, UK address as before?".
KOL replies "yes". Branch HANDLE_RESPONSE writes
`fulfillment.address_collected=true` + snapshot of address.

### New address with drift
Default was London. KOL replies "I moved — please send to <Berlin
address>". Skill writes
`fulfillment.shipping_address=<Berlin>` (masked in body) +
`approval.identity_drift_review={"old":"...London","new":"...Berlin","decision":"pending"}`.
ApprovalsPage will surface it for the operator.

### Skipped — no product
`offer.compensation_mode="commission_no_product"`. Skill aborts
`{"skipped":"no_shipment_required"}`.

## Pitfalls
- Echoing the full street in the body. Mask to city/country.
- Silently overwriting `kol_identity.default_shipping_address` when
  KOL provides a new one. Always file a drift review for the
  archival writer.
- Asking for all fields when default exists — that defeats the
  one-line UX.
- Marking `address_collected=true` from a partial address; let the
  follow-up close the loop first.
