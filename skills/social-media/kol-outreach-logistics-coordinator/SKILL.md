---
name: kol-outreach-logistics-coordinator
description: STUB skill. Advances a KOL card from contract signed to logistics pending and surfaces the address/tracking inputs to the human operator in the external Web console. Does NOT call any carrier API and does NOT email the KOL. Skill structure is preserved so a future real shipping integration can drop in without touching upstream contracts.
trigger: When a `contract_signed` event has been written to CAL for a KOL and the card needs to transition into the logistics stage. Not for any other stage.
tags: ["kol", "outreach", "logistics", "stub"]
---

## Goal
Move one KOL forward from `contract.signed` into `logistics.pending` and let the human operator fill the address / carrier / tracking number through the Web console. The skill emits the audit events but performs no real shipping work in the first version.

## Status
**STUB IMPLEMENTATION.** The first release does not integrate any carrier (UPS / FedEx / 顺丰 etc.). The Web console exposes form fields for `address`, `carrier`, `tracking_no`, `shipped_at`, `delivered_at`; these are pushed via the bridge `POST /api/plugins/kol-ops-bridge/logistics/update` endpoint. When the project later integrates a carrier API, only this skill's Procedure changes — the CAL schema (`address`, `carrier`, `tracking_no`, `shipped_at`, `delivered_at` payload fields) is already in place.

## Inputs (from caller)
- `campaign_id`, `kol_handle`, `card_id`, `kol_identity_id`
- `triggered_by` (`chat` | `web` | `cron`)
- `env` (`TEST` | `LIVE`)

Fail loudly if any required input is missing.

## Procedure (stub)

### Step 1 — Verify preconditions
1. Confirm CAL has a `contract_signed` event for this KOL.
2. Confirm no existing `logistics.delivered` event (avoid double-advance).

### Step 2 — Advance the card
Update the Kanban card body:
```yaml
status: logistics_pending
stage: logistics
sub_status: pending
last_action_at: <iso8601>
```

### Step 3 — Write CAL audit
Call `cal.record_event` with:
- `event_type: logistics_pending`
- `stage: logistics`, `sub_status: pending`
- `payload: {note: 'awaiting operator to record address and tracking'}`

### Step 4 — Notify operator
Post a single notification:
- "Logistics stage opened for @<handle> (campaign <id>). Please record shipping address and tracking in the console."
- Deep link to the KOL detail page.

Do NOT email the KOL. Do NOT request the address from the KOL via this skill — the operator either already has it or solicits it through their existing CRM.

## Hard Rules (stub)
- Zero outbound emails.
- Zero external HTTP calls (no carrier API, no address validation API).
- All sub-status transitions after `pending` (address_collected / tracking_filled / in_transit / delivered) are pushed by the Web operator, not produced by this skill.
- Every transition reflected in `kol_conversation_events`.

## Future (real-adapter expectations)
- Replace Step 2 with a real carrier call (label creation, address validation).
- Add a Step 5 polling job for tracking events; the CAL `logistics_in_transit` / `logistics_delivered` events will then come from this skill instead of the Web operator.
- Schema fields `carrier`, `tracking_no`, `shipped_at`, `delivered_at` need no change.

## Pitfalls
- Do not retro-fit shipping into the negotiation skill or the orchestrator; isolation is the whole point of the stub.
- Do not collapse pending / address_collected / tracking_filled into one sub_status — the Web UI relies on the three-step transition to render progress.
