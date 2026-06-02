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

## Shared Blocks (Phase 2)
- Runtime/draft guardrails:
  `references/shared/runtime-draft-guardrails.md`
- Style preamble baseline:
  `references/shared/style-and-brief-preambles.md`
- Reply envelope contract:
  `references/shared/reply-envelope-contract.md`

## Runtime Contract
- Follow shared runtime rules in
  `references/shared/runtime-draft-guardrails.md`.
- **Strategist is the only number source.** Do NOT invent your own
  counter; pass inputs to `kol-pricing-strategist`, take its output.
- **Hard gate.** When `requires_human_gate=true`, do NOT draft a
  numerical reply; open an escalation and reply (if at all) with the
  strategist's holding line.
- **deliverables_scope must be satisfied first.** If not, abort
  `{"skipped":"deliverables_not_scoped"}`. Defense-in-depth.
- **Idempotent on agreed.** If `goals.compensation_negotiation.status == "satisfied"`,
  abort `{"skipped":"already_agreed"}`.
- **Preserve dollar amounts exactly.** Draft text, strategist notes, and
  any temporary JSON containing values like `$3000`, `$1500`, or `$800`
  must be written with Python `json.dump` or a quoted heredoc
  (`cat <<'JSON' > /tmp/draft.json`). Never put those payloads in an
  unquoted heredoc or inline double-quoted shell string; bash expands
  `$3000` to `000` and `$800` to `00`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `inbound_excerpt` (the KOL's compensation message).
3. Optional `kol_quoted_amount`, `kol_quoted_currency`,
   `kol_quoted_basis`, `kol_mode_signal` (extracted by classifier
   into `facts_extracted.offer`).
4. `thread_history` (mandatory; may be `[]`) — JSON array of prior
   turns, oldest first, from `kol-reply-dispatcher` Step 0. Each
   entry: `{from, date, body}` only.
5. `flow_hint` — small JSON from dispatcher Step 5:
   `{lane, current_goal, next_goal_in_lane,
   missing_facts_for_current_goal, kol_signaled_next_step}`.

## Email Style Preamble (mandatory before drafting)

Follow shared style-preamble baseline in
`references/shared/style-and-brief-preambles.md`.

Call contract:
- inputs: `goal_brief = {goal: "compensation_negotiation", missing_facts: ["offer.compensation_mode", "offer.agreed_terms"], next_action: "<counter-offer / accept / decline rationale>"}`,
  `current_user_id = <operator id from session>`.

>>> include: kol-email-style-loader

## Conversation History Preamble (mandatory before drafting)

After the style-loader block, prepend a `[P0.3] Conversation history`
section, built verbatim from `thread_history` (oldest → newest):

```
[P0.3] Conversation history so far (oldest first; latest_email is
shown separately under [INBOUND]):

— <from> · <date>
<body>
...
```

When `thread_history` is `[]`, render
`[P0.3] Conversation history so far: (none).`

Hard rules (verbatim):

1. Do **not** re-quote a number that we already proposed in an
   earlier outbound turn unless the strategist explicitly raised it.
   If the strategist returned the same target as a prior outbound,
   frame this turn as restating / clarifying, not as a fresh offer.
2. Do **not** re-state the scope (platforms / count / usage rights)
   that both sides already aligned on earlier — assume it agreed
   and reference it briefly ("for the 1 IG + 1 TT we agreed on").
3. Carry forward any compensation-relevant fact the KOL volunteered
   in a prior turn (preferred currency, payout method, must-have
   clauses). Do not re-ask.

## Flow Guidance Preamble (mandatory before drafting)

Immediately after `[P0.3]`, prepend `[P0.4] Flow guidance` from
`flow_hint`:

```
[P0.4] Flow guidance:
- Lane: <lane>
- Current goal in this lane: <current_goal>
- Single fact this reply should help us collect/confirm:
  <first item of missing_facts_for_current_goal>
- Next goal in this lane (if conversation naturally arrives there):
  <next_goal_in_lane>
- KOL signaled readiness to move on: <kol_signaled_next_step>
```

Hard rules (verbatim):

1. Standard lane order is a **default**, not a forced march. When
   the KOL is actively counter-offering on numbers, stay on
   `compensation_negotiation` regardless of
   `kol_signaled_next_step`.
2. When **all of** (a) the strategist returned `gate=false` and
   the KOL has accepted (or our counter is the close), (b)
   `kol_signaled_next_step` is `true`, and (c) `next_goal_in_lane`
   is non-null — the reply may add a one-sentence handoff toward
   `next_goal_in_lane` (e.g. contract). Do **not** preview contract
   terms here.
3. Never reopen a satisfied goal upstream (scope, product) unless
   the KOL explicitly reopens it.

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
  "body": "<reply>",
  "branch": "A_draft | B_escalated",
  "strategist": { ...full strategist JSON... },
  "facts_written": {"offer": <n>}
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft` (shared:
`references/shared/reply-envelope-contract.md`).

Before returning or persisting any draft envelope, verify that money
strings still include their currency marker or explicit currency label;
outputs such as `000 quote` or `00 total` indicate shell expansion and
must be regenerated with dollar-safe JSON writing.

`strategist` is included for audit; the dispatcher logs it but
doesn't act on it.

### Refinement input (operator-triggered regeneration)
If the input includes a non-empty `operator_refinement_prompt` field,
treat it as a hard constraint on the **content** of the new draft
(tone, what to add, what to remove, specific phrasing to use).
The strategist's mode and numbers should still be respected; the
prompt only shapes the prose. Do **not** rewrite `offer.*` facts on
a refinement run — return the new envelope only. The strategist
block may be carried over unchanged from the prior draft if no facts
have changed.

## Fragment mode (multi-goal dispatch)

When input includes `fragment_mode: true`:

- Still invoke `kol-pricing-strategist` for numbers (deterministic source).
- **Do not** call `write-facts-multi` or `open-escalation`.
- **Do not** include greeting, sign-off, `to`, or `subject`.
- **Branch A (gate=false):** return compensation-only fragment:

```json
{
  "fragment_mode": true,
  "goal": "compensation_negotiation",
  "skill": "kol-compensation-negotiator",
  "fragment": "<1-3 sentences: comp terms only, include proposed number when paid>",
  "proposed_facts": {
    "offer.compensation_mode": "gifted",
    "offer.proposed_amount": 1050.0,
    "offer.proposed_basis": "flat",
    "offer.proposed_currency": "USD"
  },
  "branch": "A_draft",
  "strategist": { "...full strategist JSON..." }
}
```

- **Branch B (gate=true):** return
  `{"fragment_mode": true, "gate": true, "reason": "<gate_reason>", "goal": "compensation_negotiation", "strategist": {...}}`.
- Omit `proposed_amount` / basis / currency for pure gifted mode.
- **Never** include `offer.agreed_terms` in `proposed_facts` — that
  satisfies `compensation_negotiation` immediately; classifier sets it
  when the KOL accepts.
- `proposed_facts` keys MUST match `fact-ownership.md` for
  `compensation_negotiation`.

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
- Losing dollar signs via shell expansion when writing draft JSON. Use
  quoted heredocs or Python JSON writers whenever draft text contains
  `$` amounts.
- Re-quoting a counter number already on the record in `[P0.3]` as
  if it were new — that signals to the KOL that we're not reading
  the thread.
- Previewing contract clauses while still mid-negotiation just
  because `kol_signaled_next_step=true`. One soft handoff sentence
  is the cap; full contract talk belongs to the next dispatcher
  pass and `kol-contract-coordinator`.
