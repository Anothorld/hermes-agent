---
name: kol-compensation-negotiator
description: Composes the compensation reply once deliverables_scope is satisfied. Reads dispatch-context, calls compute-compensation-offer for numbers, applies direct-KOL barter-first vs agency paid-only policy, opens escalation only for structural gaps, otherwise drafts counter and writes offer facts. Returns the draft envelope.
trigger: Invoked by `kol-reply-dispatcher` when the classifier reports `active_goals_by_lane.commerce == "compensation_negotiation"`. Requires `goals.deliverables_scope.status == "satisfied"`; otherwise aborts.
tags: ["kol", "compensation", "negotiation", "draft-generator", "commerce-lane"]
---

## Goal
Land a compensation agreement consistent with `campaign_config`
policy, KOL's stated mode/quote, contact role, and prior history. Either:
- Barter-first / rate-request / counter / accept / hold within policy → draft + write facts, OR
- Structural config gaps → open escalation (never for high quotes alone).

## Negotiation policy (HARD)

### Direct KOL (contact_role `kol` or identity_integrity `matched`/`drifted`)
1. **Never proactively offer paid.** Even when the KOL opens with a paid
   rate or "paid only", the first compensation reply MUST be a gifted/barter
   pitch — emphasize product value and that we usually work on barter.
2. After barter is attempted (`offer.barter_attempted=true`) and the KOL
   **still insists on paid but has not shared a number**, ask the KOL to
   share their sharpest cash supplement for the agreed scope
   (`negotiation_phase=rate_request`).
   Do **not** counter with a number on that turn.
3. After `offer.rate_requested=true`, wait for the KOL's quote **unless they
   already quoted earlier** (e.g. round-1 rate + round-2 paid insistence) —
   then counter immediately. Once negotiating, use internal-review /
   campaign-economics wording for high quotes and auto-counter down (never
   escalate for price alone).
4. Tone should be **friendly, creator-facing, and collaborative**: validate
   their work, keep the product-value/cash split clear, and preserve goodwill.
   Still negotiate hard: ask for the leanest workable number, anchor low, and
   do not imply there is room to stretch.

### Agency / manager (contact_role `agency`|`manager`|`assistant`, or
identity_integrity `delegated`)
When they explicitly state paid-only / share a rate card, **skip barter-first**
and negotiate paid directly with strong bargaining language and low anchors.
Tone should be **more professional and commercial**: reference campaign
economics, incremental cash fee, aligned deliverables, scope cleanliness,
revision control, and timing efficiency. Treat process simplicity as the
concession; keep the cash number firm and low.

### Both paths
- KOL quotes are treated as **pure cash supplements** on top of a **gifted
  product** — not an all-in deal price. Always anchor negotiations on
  `product_unit_price` (product value) being included at no cost.
- `paid_ceiling` is the **cash supplement cap** only (not product value).
- Optional `paid_target_budget` in `campaign_config` is the ideal cash
  supplement starting anchor. The first cash offer MUST start from this
  lowest anchor (or the configured floor), not from the KOL's quote ratio.
- The pricing engine is the **only number source** — never invent counters.
- Cash offer numbers from the engine are intentionally rounded to natural
  negotiation anchors (usually whole hundreds, sometimes tens). Preserve
  them exactly and never add decimal places.
- Counter progression is gradual: after our first cash offer, increase only
  by the engine's small step when needed. Never jump straight toward
  `paid_ceiling`, and never reveal the cap.
- If the KOL asks about collaboration mode / paid vs gifted before we are in
  a paid-only negotiation turn, answer only that we usually work through
  product gifting / barter. Do not volunteer paid, hybrid, budget, or cash
  supplement in that answer.
- Use aggressive but polite negotiation tone; anchor on product value,
  scope discipline, and budget constraints; never reveal `paid_ceiling`.
- Split tone by contact:
  - Direct KOL: warmer, more human, creator-friendly, but still low-anchor.
  - Agency/manager: more formal, commercial, and procurement-like.
- Sound like a professional commercial negotiator: acknowledge once, separate
  product value from cash, reframe around campaign economics, state a clear
  workable number, and offer process efficiency as the concession.
- Apply negotiation tactics in the final prose:
  - Acknowledge their rate/preference once, then pivot to our structure.
  - Anchor on product value, light scope, simple process, and long-term fit.
  - Frame the counter as the workable budget for **this campaign/round**, not
    as the company's maximum.
  - Offer non-cash concessions only when useful: smoother timeline, clean
    brief, limited revisions, product experience, or future collaboration.
  - Avoid eager language such as "we can stretch more", "our max budget", or
    "we really need this"; preserve negotiating leverage.

## Shared Blocks (Phase 2)
- Runtime/draft guardrails:
  `references/shared/runtime-draft-guardrails.md`
- Style preamble baseline:
  `references/shared/style-and-brief-preambles.md`
- Reply envelope contract:
  `references/shared/reply-envelope-contract.md`
- Learning hints (reject few-shot from dispatch context):
  `../kol-reply-dispatcher/references/shared/learning-hints.md`

## Runtime Contract
- Follow shared runtime rules in
  `references/shared/runtime-draft-guardrails.md`.
- **Engine is the only number source.** Do NOT invent your own counter; call
  `compute-compensation-offer` via the bridge CLI and take its output.
- **Hard gate (structural only).** When `requires_human_gate=true`, open an
  escalation for missing config — **not** for high quotes (those always
  auto-counter).
- **Rate request is not escalation.** When `negotiation_phase=rate_request`,
  ask for the KOL's cash supplement on top of the gifted product and write
  `offer.rate_requested=true`; do NOT open an escalation and do NOT quote a
  number.
- **deliverables_scope must be satisfied first.** If not, abort
  `{"skipped":"deliverables_not_scoped"}`. Defense-in-depth.
- **Idempotent on agreed.** If `goals.compensation_negotiation.status == "satisfied"`,
  abort `{"skipped":"already_agreed"}`.
- **Defer to contract (default).** When `campaign_config.defer_terms_to_contract`
  is true (default), `goals.deliverables_scope.status == "satisfied"`, and
  `offer.compensation_mode` is `gifted` / `gifted_no_product` with no open
  paid/rate dispute (`offer.kol_paid_quote`, `paid_only_stance`, etc. absent),
  abort `{"skipped":"defer_to_contract"}` — do **not** send another email
  re-confirming the full package; the dispatcher should route
  `kol-contract-coordinator` next.
- **Preserve dollar amounts exactly.** Draft text, engine notes, and any
  temporary JSON containing values like `$3000`, `$1500`, or `$800` must be
  written with Python `json.dump` or a quoted heredoc
  (`cat <<'JSON' > /tmp/draft.json`). Never put those payloads in an
  unquoted heredoc or inline double-quoted shell string; bash expands
  `$3000` to `000` and `$800` to `00`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `inbound_excerpt` (the KOL's compensation message).
3. Optional `kol_quoted_amount`, `kol_quoted_currency`,
   `kol_quoted_basis`, `kol_mode_signal` (extracted by classifier
   into `facts_extracted.offer`, typically as `offer.kol_paid_quote`).
4. `thread_history` (mandatory; may be `[]`) — JSON array of prior
   turns, oldest first, from `kol-reply-dispatcher` Step 0. Each
   entry: `{from, date, body}` only.
5. `flow_hint` — small JSON from dispatcher Step 5:
   `{lane, current_goal, next_goal_in_lane,
   missing_facts_for_current_goal, kol_signaled_next_step}`.
6. `learning_hints` — passed through by the dispatcher (reject few-shot +
   `reply_strategy` + `company_style`).

## Learning hints (advisory)

Read `learning_hints` per
`../kol-reply-dispatcher/references/shared/learning-hints.md`. Apply
`reply_strategy` / `reply_learning` / `company_style` for
`compensation_negotiation` as advisory wording/sequencing only. **Priority on
conflict:** fact ownership > pricing engine output > escalation gates > the
HARD negotiation policy above > learning hints >
`references/learned/compensation_negotiation.md` (if promoted) > CAL
`personalization_hint` > Hindsight recall (episodic) > MEMORY.md. Never let a
hint trigger a proactive paid offer, change a pricing-engine number, or bypass
a gate. When
`references/learned/compensation_negotiation.md` exists, treat it as an
auto-promoted, advisory playbook under the same priority. Before drafting, if
that file exists under this skill directory, read it via `skill_view`.

## Email Style Preamble (mandatory before drafting)

Follow shared style-preamble baseline in
`references/shared/style-and-brief-preambles.md`.

Call contract:
- inputs: `goal_brief = {goal: "compensation_negotiation", missing_facts: ["offer.compensation_mode", "offer.agreed_terms"], next_action: "<barter-first / hold / counter-offer rationale>"}`,
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
   earlier outbound turn unless the engine explicitly raised it.
   If the engine returned the same target as a prior outbound,
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
2. When **all of** (a) the engine returned `gate=false` and
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
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE> --view agent
```
Verify `goals.deliverables_scope.status == "satisfied"` and
`goals.compensation_negotiation.status == "active"`.

Read from response:
- `campaign_config.product_unit_price`, `paid_ceiling`, `paid_target_budget`,
  `commission_band_json` (parse JSON), `barter_policy`.
- `relationship.preferred_mode`, `avg_revision_rounds`,
  `last_outcome`.
- `identity_facts` → `identity.contact_role` (if present).
- `candidate.payload` and `identity_facts` → audience tier signals:
  `creator_tier` / `kol_tier` or follower fields (`follower_count`,
  `followers`, `fans_count`). Pass these through to the engine when present;
  do not infer pricing numbers in the skill.
- `campaign_facts` → `offer.barter_attempted`, `offer.rate_requested`,
  `offer.compensation_mode`, `offer.proposed_amount`, `offer.kol_paid_quote`.
  (`offer.paid_hold_sent` is legacy alias for `rate_requested`.)

Also read `anomaly_signals.identity_integrity.status` from dispatcher
input for contact-type resolution.

If `paid_ceiling` is null AND classifier says mode=paid AND contact is
agency/manager → abort
`{"error":"campaign_config_incomplete","missing":["paid_ceiling"]}`.

Build engine payload flags from `campaign_facts`:
- `barter_attempted` ← `campaign_facts["offer.barter_attempted"]`
- `rate_requested` ← `campaign_facts["offer.rate_requested"]` or legacy
  `offer.paid_hold_sent`
- `prior_proposed_amount` ← `campaign_facts["offer.proposed_amount"]` when
  present, so the engine can increase slowly instead of restarting the round.

### Step 2 — Invoke pricing engine
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py compute-compensation-offer \
  --json '<payload>'
```

Payload MUST include:
```json
{
  "mode": "gifted|paid|commission|hybrid",
  "kol_quoted_amount": 1500.0,
  "kol_quoted_currency": "USD",
  "kol_quoted_basis": "flat",
  "campaign_config": { "...from dispatch context..." },
  "relationship": { "...from dispatch context..." },
  "contact_type": "direct|agency|manager",
  "identity": {"contact_role": "kol|agency|manager"},
  "identity_integrity": "matched|drifted|delegated|unknown",
  "candidate": {"payload": {"follower_count": 120000, "creator_tier": "mid_tier"}},
  "identity_facts": {"identity.followers": "12万"},
  "follower_count": 120000,
  "creator_tier": "mid_tier",
  "barter_attempted": true,
  "rate_requested": false,
  "paid_hold_sent": false,
  "prior_proposed_amount": 500,
  "kol_insists_paid": true
}
```

Derive `contact_type` / `identity_integrity` from dispatch context +
classifier facts. Pass `barter_attempted` / `rate_requested` from
`campaign_facts` (see Step 1). Set `kol_quoted_amount` in the engine
payload from, in order: (1) classifier `facts_extracted.offer["offer.kol_paid_quote"]`
from this inbound, (2) dispatcher-passed `kol_quoted_amount`, (3)
`campaign_facts["offer.kol_paid_quote"]`. Set `kol_insists_paid=true`
when classifier emitted `paid_only_stance` or the inbound clearly rejects
barter / repeats paid-only.

Audience-tier fields are optional and must be copied from dispatch context
only when available:
- Prefer explicit `candidate.payload.creator_tier` / `kol_tier` or
  `identity_facts["identity.creator_tier"]`.
- Otherwise pass follower count aliases from `candidate.payload` or
  `identity_facts` (`follower_count`, `followers`, `fans_count`).
- The engine maps `<50k` to `koc`, `50k-300k` to `mid_tier`, and `>300k`
  to `top_tier`. If all tier inputs are absent, the engine falls back to
  the legacy campaign-budget strategy.
- Never change the returned `target_number` for tone or tier reasons.

Receive the engine JSON (see `kol-pricing-strategist` SKILL for shape).
Key field: `negotiation_phase` ∈
`barter_first | rate_request | paid_counter | escalate | null`.

### Step 3 — Branch on `requires_human_gate` and `negotiation_phase`

**Branch A — gate=false, draft (includes barter_first and rate_request):**
- Body uses `suggested_wording` from engine as the spine, customizing
  for tone (greeting, sign-off). Apply strong negotiation polish via
  humanizer without changing numbers or policy facts.
- For `negotiation_phase=paid_counter` or paid/hybrid with a number,
  body MUST include the proposed terms explicitly.
- On the **first** cash counter (no prior `offer.proposed_amount`), the
  engine returns the single-test intro-budget template (manager-locked
  budget, future-campaign priority). Preserve that spine and its numbers
  exactly; humanizer may only add greeting/sign-off. Never add a
  parenthetical discount vs the KOL quote (e.g. "about 48% of your quoted rate").
- For `negotiation_phase=barter_first` or `rate_request`, body MUST NOT
  include a paid counter number.
- Humanizer may make the wording warmer, but must keep these negotiation
  moves intact: one acknowledgment, a firm pivot to our structure, a bounded
  offer for this campaign, and no hint of `paid_ceiling`.

**Branch B — gate=true (escalate — structural gaps only):**
```
kol_bridge_tool.py open-escalation --env <TEST|LIVE> \
  --json '{"identity_id":...,"campaign_id":"...",
            "goal":"compensation_negotiation",
            "reason":"<engine gate_reason>",
            "operator_note":"<inbound_excerpt> | structural gap"}'
```
Use only for `missing_paid_ceiling`, `missing_commission_band`, etc.
**Never** for high KOL quotes.

### Step 4 — Write outbound facts (Branch A only)
```
write-facts-multi --json '{
  "campaign_id":"...",
  "source":"skill:kol-compensation-negotiator",
  "namespaces":{
    "offer":{
      "offer.compensation_mode": "<gifted|paid|commission|hybrid>",
      "offer.proposed_amount": 800,
      "offer.proposed_basis": "flat",
      "offer.proposed_currency": "USD",
      "offer.barter_attempted": true,
      "offer.rate_requested": true
    }
  }
}'
```

Phase-specific fact writes:
| `negotiation_phase` | Facts to write |
|---|---|
| `barter_first` | `offer.compensation_mode=gifted`, `offer.barter_attempted=true`; omit `proposed_amount` |
| `rate_request` | `offer.rate_requested=true`; omit `proposed_amount` |
| `paid_counter` | full `offer.compensation_mode` + `proposed_*` as applicable |

Do NOT set `offer.agreed_terms` here — that flips on a future inbound
where classifier confirms KOL accepted.

For pure gifted mode with no number, omit `proposed_amount` /
`proposed_basis` / `proposed_currency`.

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
  "strategist": { "...full engine JSON..." },
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
The engine's mode and numbers should still be respected; the prompt
only shapes the prose. Do **not** rewrite `offer.*` facts on
a refinement run — return the new envelope only.

## Fragment mode (multi-goal dispatch)

When input includes `fragment_mode: true`:

- Still call `compute-compensation-offer` for numbers (deterministic source).
- **Do not** call `write-facts-multi` or `open-escalation`.
- **Do not** include greeting, sign-off, `to`, or `subject`.
- **Branch A (gate=false):** return compensation-only fragment:

```json
{
  "fragment_mode": true,
  "goal": "compensation_negotiation",
  "skill": "kol-compensation-negotiator",
  "fragment": "<1-3 sentences: comp terms only>",
  "proposed_facts": {
    "offer.compensation_mode": "gifted",
    "offer.barter_attempted": true
  },
  "branch": "A_draft",
  "strategist": { "...full engine JSON..." }
}
```

- **Branch B (gate=true):** return
  `{"fragment_mode": true, "gate": true, "reason": "<gate_reason>", "goal": "compensation_negotiation", "strategist": {...}}`.
- Include `proposed_amount` only when `negotiation_phase=paid_counter`.
- **Never** include `offer.agreed_terms` in `proposed_facts`.
- `proposed_facts` keys MUST match `fact-ownership.md` for
  `compensation_negotiation`.

## Examples

### Direct KOL — barter first despite paid quote
KOL: "I work only paid, $1500 flat". Contact: direct KOL, no prior barter.
Engine returns `{mode_decided:gifted, negotiation_phase:barter_first}`.
Skill drafts gifted/barter pitch + writes
`offer.compensation_mode=gifted`, `offer.barter_attempted=true`.

### Direct KOL — ask for rate after insistence
Prior: barter attempted. KOL: "No thanks, I need to be paid."
Engine returns `{negotiation_phase:rate_request, target_number:null}`.
Skill asks for the KOL's cash supplement + writes `offer.rate_requested=true`.
No paid number.

### Direct KOL — paid counter after prior quote
Prior: barter attempted. KOL already quoted $1500 cash in round 1; round 2
insists paid. Engine returns `{negotiation_phase:paid_counter, target_number:800}`
immediately (no rate_request). High quotes use internal-review /
campaign-economics wording + auto-counter.

### Agency — high quote auto-counter
Agency: "$3000 cash". `paid_ceiling=1500`. Engine returns
`{target_number:900, negotiation_phase:paid_counter}` with campaign-economics
wording; no escalation.

## Pitfalls
- Drafting a paid counter for a direct KOL before barter-first completes.
- Escalating on high quotes — always auto-counter instead.
- Treating `rate_request` as escalation — it is a normal draft branch.
- Countering before barter-first when `barter_attempted` is false.
- Drafting a number without calling the engine.
- Setting `offer.agreed_terms=...` on the basis of our own counter.
- Softening or raising engine numbers in the draft prose.
- Re-quoting a counter number already on the record as if it were new.
