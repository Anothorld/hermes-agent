---
name: kol-contract-coordinator
description: Handles the contract phase after compensation is agreed. Three branches — (1) initiate: send the contract draft (subject + body referencing the agreed terms), write `offer.contract_sent=true`; (2) chase: nudge a KOL who hasn't signed within the configured follow-up window; (3) handle response: when classifier extracts `offer.contract_signed=true` (acknowledged), no draft needed — just write the fact; when KOL asks to change a CORE clause (exclusivity, IP, payment terms), open escalation and write `approval.contract_change_request`. Skipped automatically when `campaign_config.contract_required=false`.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.commerce == "contract_signing"`. Also invoked by a future cron-driven follow-up loop for chase mode (out of scope this phase). Never invoked when `goals.contract_signing.status == "skipped"` (config says no contract).
tags: ["kol", "contract", "draft-generator", "approval", "commerce-lane"]
---

## Goal
Drive the contract sub-goal from `agreed → sent → signed` (or to
`escalation` when KOL pushes back on core clauses), without ever
drafting legally-binding language ourselves. The actual contract PDF
is templated outside this plugin (deferred); this skill drives the
**email layer** and the **fact recording**.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Never draft contract clauses.** Body says "see attached" or
  "DocuSign link will follow"; the contract content itself is owned
  by Legal.
- **Core-clause changes are always escalation.** Anything touching
  exclusivity, IP/usage, payment terms, term length, governing law
  → escalation, not negotiation. Cosmetic edits (typos, name
  spelling, address) → write `approval.contract_change_request`
  with `severity=low` and proceed.
- **Skip when not required.** If `goals.contract_signing.status == "skipped"`,
  abort `{"skipped":"contract_not_required"}`.
- **Idempotent.** If `goals.contract_signing.status == "satisfied"`,
  abort `{"skipped":"already_signed"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `mode`: one of `initiate | chase | handle_response`.
3. `inbound_excerpt` (only for `handle_response`).
4. Classifier-extracted facts:
   `offer.contract_signed_signal` (`signed | declined | change_requested | silent`),
   `offer.contract_change_kind` (`core | cosmetic | null`).

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `goals.compensation_negotiation.status` — must be `satisfied`
  (defense-in-depth; aborts otherwise).
- `goals.contract_signing.status`.
- `campaign_config.contract_required` — must be `true`.
- Latest `offer.compensation_mode`, `offer.agreed_terms` — for
  templating the email reference.

### Step 2 — Branch on `mode`

**Branch I — initiate:**
Body skeleton:
> "Great, glad we're aligned. I'll send the agreement over via
> `<channel>` with the terms we discussed (`<one-line summary of
> agreed terms>`). Should land in your inbox within 1 business day."

Write:
```
write-facts-multi --json '{
  "campaign_id":"...","source":"skill:kol-contract-coordinator",
  "namespaces":{
    "offer":{"offer.contract_sent": true,
              "offer.contract_sent_at": "<iso8601>"}
  }
}'
```

**Branch C — chase:**
Body skeleton:
> "Just bumping this — let me know if anything in the agreement is
> blocking. Happy to walk through any clause."

Do NOT write a fact for chase (no state change); the
`offer.contract_chase_count` could be incremented in a future
extension (deferred — not in this phase).

**Branch R — handle_response:**

| `contract_signed_signal` | `contract_change_kind` | Action |
|---|---|---|
| `signed` | n/a | write `offer.contract_signed=true` + `offer.contract_signed_at`; no draft, return `{"acknowledged_only": true}` |
| `declined` | n/a | open escalation `goal=contract_signing reason="KOL declined to sign"` + write `offer.contract_declined_reason=<excerpt>` |
| `change_requested` | `core` | open escalation `goal=contract_signing reason="KOL requested core-clause change: <excerpt>"` + write `approval.contract_change_request={"kind":"core","excerpt":"...","decision":"pending"}` |
| `change_requested` | `cosmetic` | write `approval.contract_change_request={"kind":"cosmetic","excerpt":"...","decision":"pending"}` (severity low — operator approves async); draft a holding reply: "noted, will get the cosmetic update in" |

When opening escalation, omit Step 4 facts that conflict (e.g. don't
mark `contract_sent` when handling response).

### Step 3 — (Branch I/C only) Compose the email
Branch I/C produce a body. Branches that escalate or just
acknowledge return `body: null`.

### Step 4 — Write fact (per Step 2 table)
Each row prescribes its own fact set; emit one
`write-facts-multi` call per row, atomic.

### Step 5 — Return draft envelope
```json
{
  "skill": "kol-contract-coordinator",
  "mode": "initiate | chase | handle_response",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": null,
  "body": "<reply or null>",
  "branch_action": "drafted | escalated | acknowledged_only | cosmetic_pending_approval",
  "facts_written": {"offer": 1, "approval": 1},
  "escalation_opened": false
}
```

## Examples

### Branch I
KOL just agreed on $1050 flat; classifier said
`compensation_negotiation` advanced to satisfied. Coordinator drafts
"I'll send the agreement... 1 business day" and writes
`offer.contract_sent=true`.

### Branch R — core change
Inbound: "Looks good, but can we cap exclusivity at 30 days?"
Classifier extracts `change_kind=core`. Coordinator opens
escalation + writes `approval.contract_change_request.kind=core`.

### Branch R — cosmetic
Inbound: "All good — please change my legal name to <X>."
Classifier extracts `change_kind=cosmetic`. Coordinator writes
`approval.contract_change_request.kind=cosmetic` + drafts holding
reply. Operator approves later via ApprovalsPage.

### Skipped
`campaign_config.contract_required=false`. Skill aborts
`{"skipped":"contract_not_required"}`.

## Pitfalls
- Drafting actual clause language. Always defer to "the agreement"
  / "the document" / "Legal will share the full terms".
- Treating cosmetic vs core changes the same way. Cosmetic changes
  don't deserve an escalation — they create approval entries
  instead.
- Marking `contract_signed=true` on the basis of "I'm in!" alone —
  must come from classifier-confirmed signed signal (e.g. a
  DocuSign completion email or explicit "I've signed and returned").
- Forgetting to mark the `*_at` timestamp; downstream cron uses it
  to compute follow-up windows.
