---
name: kol-logistics-tracker
description: After address is collected, drives the package state from `address → tracking → delivered`. Three modes — (1) send_tracking: when ops fills in the tracking number internally (out-of-band), this skill drafts the "your package is on the way" email and writes `fulfillment.tracking_no` + `fulfillment.tracking_filled=true`; (2) chase_delivery: nudge a KOL whose package was shipped > N days ago without a delivered confirmation; (3) handle_response: KOL says "received it" → write `fulfillment.delivered_confirmed=true`; KOL says "not received / damaged / wrong item" → open escalation. Never invents tracking numbers.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.fulfillment == "logistics"` AND `fulfillment.address_collected == true`. Also invoked by a future cron with `mode=chase_delivery` (deferred).
tags: ["kol", "logistics", "tracking", "delivered", "draft-generator", "fulfillment-lane"]
---

## Goal
Move logistics from `address_collected` to `delivered_confirmed`
through three explicit modes; surface any anomaly as an escalation
rather than freelancing a fix.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Never invent tracking numbers.** `mode=send_tracking` requires a
  caller-supplied `tracking_no` and `carrier`; abort if missing.
- **Anomalies escalate, never freelance.** Damage / loss / wrong-item
  → escalation, not "let me check with the courier".
- **Idempotent.** Aborts when target fact is already true (e.g.
  `tracking_filled=true` for send_tracking, `delivered_confirmed=true`
  for handle_response).

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `mode`: `send_tracking | chase_delivery | handle_response`.
3. For `send_tracking`: `tracking_no` (string), `carrier` (string),
   optional `tracking_url` (string).
4. For `handle_response`: `inbound_excerpt` + classifier-extracted
   `fulfillment.delivery_signal` ∈ `{received | not_received | damaged | wrong_item | silent}`.

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "logistics", missing_facts: [<from goal_state>], next_action: "<send tracking / chase delivery / handle response>"}`,
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
Read `goals.logistics.status` and the latest fulfillment facts
(`tracking_filled`, `delivered_confirmed`).

### Step 2 — Branch on `mode`

**Branch ST — send_tracking:**
- Validate `tracking_no` and `carrier` are non-empty strings; else
  abort `{"error":"tracking_inputs_missing"}`.
- Idempotent guard: if `fulfillment.tracking_filled==true`, abort
  `{"skipped":"already_sent"}`.
- Body skeleton:
  > "Your `<carrier>` shipment is on its way! Tracking number:
  > `<tracking_no>` `<( "Track here: " + tracking_url ) if url else "">`.
  > Drop me a line when it lands."
- Write:
  ```
  "fulfillment":{"fulfillment.tracking_no": "<tracking_no>",
                  "fulfillment.tracking_carrier": "<carrier>",
                  "fulfillment.tracking_url": "<url or null>",
                  "fulfillment.tracking_filled": true,
                  "fulfillment.tracking_sent_at": "<iso8601>"}
  ```

**Branch CD — chase_delivery:**
- Body: "Just checking — has the package landed yet? Let me know if
  there's any issue."
- Write nothing (no state change). Future extension may increment
  `fulfillment.delivery_chase_count`.

**Branch HR — handle_response:**

| `delivery_signal` | Action |
|---|---|
| `received` | write `fulfillment.delivered_confirmed=true` + `..._at`; draft acknowledgement "Awesome, glad it landed safely!" |
| `not_received` | if `tracking_sent_at` < N days ago → draft "give it another day or two, ping me if it doesn't show"; if older → open escalation `goal=logistics reason="package not received N+ days post-ship"` |
| `damaged` | open escalation `goal=logistics reason="KOL reports damaged package: <excerpt>"` + write `approval.shipping_anomaly={"kind":"damaged","excerpt":"...","decision":"pending"}` |
| `wrong_item` | open escalation `goal=logistics reason="KOL reports wrong item: <excerpt>"` + write `approval.shipping_anomaly={"kind":"wrong_item",...}` |
| `silent` | (classifier should not have routed here; defense) abort `{"skipped":"no_delivery_signal"}` |

`N` for the not-received threshold defaults to 7; future extension
will read it from `campaign_config.followup_intervals.undelivered_days`.

### Step 3 — Compose email (when drafting)
Branches ST / CD / HR(received) / HR(not_received_recent) draft.
Anomaly branches return `body: null` (the escalation is the action;
the email comes from the escalation resumer later).

### Step 4 — Write facts per Step 2

### Step 5 — Return draft envelope
```json
{
  "skill": "kol-logistics-tracker",
  "mode": "send_tracking | chase_delivery | handle_response",
  "branch_action": "tracking_sent | delivery_chased | delivered | not_yet_chased | escalated_anomaly",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "body": "<reply or null>",
  "facts_written": {"fulfillment": <n>, "approval": <n>},
  "escalation_opened": false
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft`.

## Examples

### send_tracking
Ops fills tracking via Web. Skill drafts "your DHL shipment is on its
way! Tracking: 1Z123..." and writes 5 fulfillment facts.

### handle_response — damaged
Inbound: "Hey, the rug arrived but it's torn at the corner."
Classifier extracts `delivery_signal=damaged`. Skill opens escalation
+ writes `approval.shipping_anomaly.kind=damaged`. No body drafted.

### Idempotent
`tracking_filled=true` already. Branch ST aborts
`{"skipped":"already_sent"}`.

## Pitfalls
- Inventing tracking numbers when ops haven't supplied them. Hard
  abort on missing inputs.
- Treating "haven't received it" the same regardless of how long the
  package has been in transit. Time-aware threshold N matters.
- Silently re-sending tracking on every dispatcher pass. Idempotency
  check on `tracking_filled` is mandatory.
- Drafting a body for damage/wrong-item branches. Those are the
  escalation resumer's territory.
