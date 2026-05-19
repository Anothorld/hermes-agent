---
name: kol-outreach-contract-coordinator
description: STUB skill. Advances a KOL card from negotiation accepted to contract pending and prompts the human to handle signing in the external Web console. Does NOT send any contract email and does NOT call DocuSign / e-sign APIs. The full skill structure is in place so that the future real adapter can replace only the Procedure section without touching upstream skills or schemas.
trigger: When the orchestrator detects that a negotiation has been accepted (decision == accept) and the KOL card needs to transition into the contract stage. Not for any other stage.
tags: ["kol", "outreach", "contract", "stub"]
---

## Goal
Move one KOL forward from `negotiation.accepted` into `contract.pending` and emit the audit events the Web console relies on. The actual signing flow is operated by a human in the external KOL Ops Console; this skill does NOT email, sign, or upload anything in the first version.

## Status
**STUB IMPLEMENTATION.** The first release intentionally does not integrate any e-sign provider. The Web console exposes a "mark contract signed" button that calls the bridge `POST /api/plugins/kol-ops-bridge/contract/update` endpoint with `sub_status: signed`. When the project later integrates DocuSign / PandaDoc / etc., the implementation of this skill is the only place that needs to change — the upstream orchestrator, downstream logistics skill, CAL schema, bridge API, and Web UI remain untouched.

## Inputs (from caller)
- `campaign_id`, `kol_handle`, `card_id`, `kol_identity_id`
- The accepted negotiation amount + currency (from the latest `kol_negotiation_history` row with `decision == 'accept'`)
- `triggered_by` (`chat` | `web` | `cron`)
- `env` (`TEST` | `LIVE`)

Fail loudly if any required input is missing; never invent values.

## Procedure (stub)

### Step 1 — Verify preconditions
1. Confirm the latest negotiation row for this KOL has `decision == 'accept'` and `human_decision` is `accept` or null. If the negotiation is still under human review, refuse and tell the caller to wait.
2. Confirm no existing `contract.signed` event exists in CAL for this KOL (avoid double-advance).

### Step 2 — Advance the card
1. Update the Kanban card body:
   ```yaml
   status: contract_pending
   stage: contract
   sub_status: pending
   last_action_at: <iso8601>
   ```
2. Append a comment to the Kanban card: `Contract stage entered. Operator action required in KOL Ops Console.`

### Step 3 — Write CAL audit
Call `cal.record_event` with:
- `event_type: contract_pending`
- `stage: contract`, `sub_status: pending`
- `actor: <chat|web|cron>`
- `payload: {accepted_amount, currency, accepted_at, source_negotiation_seq}`

### Step 4 — Notify operator (single message)
Post one notification through the configured notifier with:
- KOL handle, campaign id, accepted amount.
- Deep link to the KOL detail page in the Web console.
- Phrase: "Contract stage opened — please prepare and send the contract via your usual channel, then mark signed in the console."

Do NOT compose a contract email draft. Do NOT add any provider-specific fields.

## Hard Rules (stub)
- Never email the KOL. The first version produces zero Gmail drafts.
- Never call any e-sign API. The first version has zero external HTTP calls.
- Never assume a sub_status other than `pending`. Signing transitions are pushed by the Web operator through the bridge, not by this skill.
- Never bypass the audit: every state advancement MUST be reflected in `kol_conversation_events`.

## Future (when the real adapter lands)
- Step 2 will additionally call the provider SDK to create an envelope.
- Step 3 will add `signed_url`, `provider`, `envelope_id` to the payload.
- A new Step 5 will start a webhook listener registration. The CAL schema already reserves these fields (`signed_url`, `provider`, etc.); no migration required.

## Pitfalls
- Do not skip Step 1's idempotency check; this skill can be re-invoked safely only if the check is honored.
- Do not collapse this skill into the orchestrator: keeping the stub separate is what makes the future swap trivial.
