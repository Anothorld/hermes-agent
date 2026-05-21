---
name: kol-compensation-negotiator
description: Composes the compensation reply once deliverables_scope is satisfied. Reads dispatch-context (campaign_config + relationship + facts), invokes `kol-pricing-strategist` for the numerical recommendation, branches by mode (gifted / paid / commission / hybrid), opens an escalation when the strategist sets `requires_human_gate=true`, otherwise drafts the counter and writes `offer.compensation_mode`, `offer.proposed_amount`, `offer.proposed_basis`, `offer.agreed_terms` (only when KOL has already agreed) etc. Returns the draft envelope.
trigger: Invoked by `kol-reply-dispatcher` when the classifier reports `active_goals_by_lane.commerce == "compensation_negotiation"`. Requires `goals.deliverables_scope.status == "satisfied"`; otherwise aborts.
tags: ["kol", "compensation", "negotiation", "draft-generator", "commerce-lane"]
---

## Goal
Land a compensation agreement consistent with `campaign_config`
policy, KOL's stated mode/quote, and prior history. Either:
- Counter / accept / hold within policy → draft + write facts, OR
- Over-policy → open escalation, do NOT draft a number.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Strategist is the only number source.** Do NOT invent your own
  counter; pass inputs to `kol-pricing-strategist`, take its output.
- **Hard gate.** When `requires_human_gate=true`, do NOT draft a
  numerical reply; open an escalation and reply (if at all) with the
  strategist's holding line.
- **deliverables_scope must be satisfied first.** If not, abort
  `{"skipped":"deliverables_not_scoped"}`. Defense-in-depth.
- **Idempotent on agreed.** If `goals.compensation_negotiation.status == "satisfied"`,
  abort `{"skipped":"already_agreed"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `inbound_excerpt` (the KOL's compensation message).
3. Optional `kol_quoted_amount`, `kol_quoted_currency`,
   `kol_quoted_basis`, `kol_mode_signal` (extracted by classifier
   into `facts_extracted.offer`).

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Verify `goals.deliverables_scope.status == "satisfied"` and
`goals.compensation_negotiation.status == "active"`.

Read from response:
- `campaign_config.product_unit_price`, `paid_ceiling`,
  `commission_band_json` (parse JSON), `barter_policy`.
- `relationship.preferred_mode`, `avg_revision_rounds`,
  `last_outcome`.

If `paid_ceiling` is null AND classifier says mode=paid → abort
`{"error":"campaign_config_incomplete","missing":["paid_ceiling"]}`.

### Step 2 — Invoke pricing-strategist
Pass the full structured input from inputs+context. Receive the
strategist JSON (see its SKILL.md for shape).

### Step 3 — Branch on `requires_human_gate`

**Branch A — gate=false (draft):**
- Body uses `suggested_wording` from strategist as the spine,
  customizing for tone (greeting, sign-off).
- Body MUST include the proposed terms explicitly — the KOL needs to
  see the number/percent so they can accept/counter.

**Branch B — gate=true (escalate):**
```
kol_bridge_tool.py open-escalation --env <TEST|LIVE> \
  --json '{"identity_id":...,"campaign_id":"...",
            "goal":"compensation_negotiation",
            "reason":"<strategist gate_reason>",
            "operator_note":"<inbound_excerpt> | KOL_quoted=<x> | ceiling=<y>"}'
```
Return `{"escalation_opened": true, ...}`. The router will not draft
this turn.

### Step 4 — Write outbound facts (Branch A only)
```
write-facts-multi --json '{
  "campaign_id":"...",
  "source":"skill:kol-compensation-negotiator",
  "namespaces":{
    "offer":{
      "offer.compensation_mode": "<gifted|paid|commission|hybrid>",
      "offer.proposed_amount": 1050.0,
      "offer.proposed_basis": "flat",
      "offer.proposed_currency": "USD"
    }
  }
}'
```

Do NOT set `offer.agreed_terms` here — that flips on a future inbound
where classifier confirms KOL accepted.

For `mode=gifted` with no number, omit `proposed_amount` /
`proposed_basis` / `proposed_currency` and write only
`offer.compensation_mode=gifted`.

### Step 5 — Return draft envelope
```json
{
  "skill": "kol-compensation-negotiator",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": null,
  "body": "<reply>",
  "branch": "A_draft | B_escalated",
  "strategist": { ...full strategist JSON... },
  "facts_written": {"offer": <n>}
}
```

`strategist` is included for audit; the dispatcher logs it but
doesn't act on it.

## Examples

### Branch A — paid counter
KOL: "I work only paid, $1500 flat for 1 IG + 1 TT".
Config: `paid_ceiling=2000`. Strategist returns
`{mode_decided:paid, target_number:1050, gate=false}`. Skill drafts
"thanks for the rate — we can stretch to $1050 flat for 1 IG + 1 TT,
how does that work?" + writes 4 offer facts.

### Branch B — over ceiling
KOL: "$3000 flat". `paid_ceiling=2000`. Strategist returns
`{requires_human_gate:true, gate_reason:"paid_quote_over_ceiling"}`.
Skill opens escalation; returns `escalation_opened=true`; no facts
written (no draft).

### Skipped — deliverables not scoped
Step 1 reveals `deliverables_scope.status="active"`. Skill aborts
`{"skipped":"deliverables_not_scoped"}` so the router runs
`kol-deliverables-clarifier` first.

## Pitfalls
- Drafting a number without invoking the strategist. Determinism
  requires the strategist be the single source of truth.
- Drafting a counter when `requires_human_gate=true`. Always
  escalate, even if the gap is small.
- Setting `offer.agreed_terms=...` on the basis of our own counter.
  That flips only on KOL acceptance.
- Forgetting `offer.proposed_currency` on paid/hybrid drafts; the
  contract-coordinator needs it.
