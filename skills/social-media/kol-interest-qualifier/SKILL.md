---
name: kol-interest-qualifier
description: Composes ONE clarifying reply when the KOL's interest signal is ambiguous after our outreach (e.g. "thanks for reaching out" / "tell me more" / "is this paid or gifted?"). Reads dispatch-context, asks exactly ONE concrete follow-up question that maps to the missing fact (`offer.interest_signal`), writes nothing about price/products yet, and returns the draft envelope. Never sends mail directly.
trigger: Invoked by `kol-reply-dispatcher` when the classifier reports `active_goals_by_lane.commerce == "interest_qualification"` with `ambiguity != null` AND no `offer.interest_signal` confirmed/declined yet. Skip if `interest_signal` is already confirmed (caller mis-routed).
tags: ["kol", "interest", "clarify", "draft-generator", "commerce-lane"]
---

## Goal
Move `interest_qualification` from ambiguous to actionable in ONE
short reply. Either:
- KOL responds with explicit confirmation/decline → next dispatcher
  pass advances commerce lane, or
- KOL responds with a concrete blocker (paid only, off-brand, busy) →
  next dispatcher pass routes to compensation-negotiator or archive.

This skill is **side-effect-light**: it writes a single fact
recording that we asked, but does NOT pre-commit interest as confirmed.

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
- **One clarifying question max.** Never bundle interest +
  product + deliverables + price into one paragraph; that's exactly
  the trap this skill exists to avoid.
- **No price talk, no SKU talk, no deliverable counts.** Those are
  later goals.
- **Collaboration mode questions:** if the KOL asks whether the collab is
  paid/gifted, asks "what kind of collaboration?", or asks the cooperation
  model, answer only that we usually work through product gifting / barter.
  Do **not** mention paid, hybrid, flexibility, budget, or cash supplement
  at this stage.
- **Idempotent on already-confirmed.** If
  `goals.interest_qualification.status == "satisfied"`, abort
  `{"skipped":"already_qualified"}`.

## Inputs
1. `identity_id` (mandatory).
2. `campaign_id` (mandatory).
3. `env` (`TEST|LIVE`).
4. `thread_id` (mandatory — this is a reply, not a fresh thread).
5. `inbound_excerpt` (1-3 sentence quote of KOL's ambiguous reply,
   for grounding the question).
6. `thread_history` (mandatory; may be `[]`) — JSON array of prior
   turns, oldest first, from `kol-reply-dispatcher` Step 0. Each
   entry: `{from, date, body}` only — no headers, ids, subjects.
7. `flow_hint` — small JSON from dispatcher Step 5:
   `{lane, current_goal, next_goal_in_lane,
   missing_facts_for_current_goal, kol_signaled_next_step}`. Used only
   for the `[P0.4]` block below.
8. `learning_hints` — passed through by the dispatcher (reject few-shot +
   `reply_strategy` + `company_style`).

## Learning hints (advisory)

Read `learning_hints` per
`../kol-reply-dispatcher/references/shared/learning-hints.md`. Apply
`reply_strategy` / `reply_learning` / `company_style` for
`interest_qualification` as advisory wording/sequencing only. **Priority on
conflict:** fact ownership > pricing engine output > escalation gates > this
skill's HARD rules > learning hints >
`references/learned/interest_qualification.md` (if promoted) > CAL
`personalization_hint` > Hindsight recall (episodic) > MEMORY.md. Hints never
add facts or change the goal state. When `references/learned/interest_qualification.md` exists, treat it as
an auto-promoted, advisory playbook under the same priority. Before drafting, if
`references/learned/interest_qualification.md` exists, read it via `skill_view`.

## Email Style Preamble (mandatory before drafting)

Follow shared style-preamble baseline in
`references/shared/style-and-brief-preambles.md`.

Call contract:
- inputs: `goal_brief = {goal: "interest_qualification", missing_facts: ["offer.interest_signal"], next_action: "Disambiguate KOL's reply with one focused question"}`,
  `current_user_id = <operator id from session>`.

>>> include: kol-email-style-loader

## Conversation History Preamble (mandatory before drafting)

After the style-loader block, prepend a `[P0.3] Conversation history`
section to the LLM prompt. Build it from the `thread_history` input
(verbatim from the dispatcher) as oldest → newest, one entry per turn:

```
[P0.3] Conversation history so far (oldest first; latest_email is
shown separately under [INBOUND]):

— <from> · <date>
<body>

— <from> · <date>
<body>
...
```

When `thread_history` is `[]`, render the block as
`[P0.3] Conversation history so far: (none — this is the first
inbound reply after our cold opener).`

Hard rules attached to this block (include them verbatim in the
prompt under the history):

1. Do **not** re-ask a question whose answer appears anywhere above.
   If the KOL already told us their platforms / paid stance / brand
   fit / availability in an earlier turn, carry it forward as known
   and ask only for what is still genuinely missing.
2. Do **not** repeat phrasing, openers, or the same explanatory
   sentence we have already used in earlier outbound turns above
   (entries `from` = us). Vary the wording naturally.
3. If the KOL has already volunteered a fact that satisfies
   `missing_facts_for_current_goal`, mention briefly that you have it
   noted instead of asking again, and either move toward the next
   step (see `[P0.4]`) or close the loop on this one.

## Flow Guidance Preamble (mandatory before drafting)

Immediately after `[P0.3]`, prepend a `[P0.4] Flow guidance` section
populated from the `flow_hint` input:

```
[P0.4] Flow guidance:
- Lane: <lane>
- Current goal in this lane: <current_goal>
- Single fact this reply should help us collect/confirm:
  <first item of missing_facts_for_current_goal, or "(none — current
  goal is mostly satisfied)">
- Next goal in this lane (if conversation naturally arrives there):
  <next_goal_in_lane | "(this is the lane's terminal goal)">
- KOL signaled readiness to move on: <kol_signaled_next_step>
```

Hard rules attached to this block (include them verbatim):

1. The standard order in this lane is a **default**, not a forced
   march. Stay on `current_goal` when the KOL's latest message asks
   about it, when the KOL is unclear, or when a required fact is
   still missing.
2. When **all of**: (a) `kol_signaled_next_step` is `true`,
   (b) the KOL did **not** raise a new question on `current_goal`,
   and (c) `next_goal_in_lane` is non-null — let the reply naturally
   transition toward `next_goal_in_lane` (a one-sentence handoff is
   fine; do not jam the whole next-stage agenda into this one mail).
3. Never **skip backwards** to an earlier goal in the lane unless
   the KOL explicitly reopened it in `latest_email` (e.g. asks to
   swap SKU after `product_selection` already satisfied).
4. The aim is to gently guide the conversation forward, not to
   railroad it. If `[P0.3]` shows we've already nudged in this
   direction and the KOL deflected, do not push again — answer
   what they raised and let the operator decide on the next move.

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE> --view agent
```
Verify `goals.interest_qualification.status == "active"`. Else abort.

### Step 2 — Pick THE question
Choose the single most informative question based on `inbound_excerpt`:

| KOL signal | Question to ask |
|---|---|
| "tell me more" | "Quick context: we usually work through product gifting for this campaign around `<one product line>`. Would that be something you'd be open to?" |
| "is this paid?" / "paid or gifted?" / "what's the collab model?" | "For this campaign, we usually work through product gifting / barter. Would you be open to that?" |
| "what brand is this?" | One-line brand pitch + "Would that be a fit for your audience?" |
| "I'm busy / next quarter" | "Totally understand — would `<month X>` work better, or shall we close this out?" |
| Generic positive ("love it!") | "Glad to hear! Just to confirm — are you up for moving forward?" |

If `inbound_excerpt` doesn't match any, default to the generic
"Just confirming — are you up for moving forward?".

### Step 3 — Write the "asked" fact
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-interest-qualifier",
            "namespaces":{
              "offer": {"offer.interest_clarify_asked": true,
                         "offer.interest_clarify_question": "<the question text>"}
            }}'
```

We do NOT set `offer.interest_signal` here — only the KOL's actual
reply (via classifier on the next inbound) sets that.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-interest-qualifier",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "<thread_id>",
  "body": "<the reply>",
  "facts_written": {"offer": 2}
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft` (shared:
`references/shared/reply-envelope-contract.md`).

## Fragment mode (multi-goal dispatch)

When input includes `fragment_mode: true`:

- Run Steps 1–2 logic only; **do not** call `write-facts-multi`.
- **Do not** include greeting, sign-off, `to`, or `subject`.
- Return one clarifying question as fragment (interest only — no price/SKU):

```json
{
  "fragment_mode": true,
  "goal": "interest_qualification",
  "skill": "kol-interest-qualifier",
  "fragment": "<one focused question on interest/collab mode>",
  "proposed_facts": {
    "offer.interest_clarify_asked": true,
    "offer.interest_clarify_question": "<the question text>"
  }
}
```

- Optional identity facts when manager detected:
  `identity.contact_role`, `identity.manager_name`, `identity.manager_email`.
- **Never** include `offer.interest_signal` in `proposed_facts` — that
  satisfies `interest_qualification` immediately; classifier sets
  confirmed/declined on a later inbound.
- `proposed_facts` keys MUST match `fact-ownership.md` for
  `interest_qualification`.

## Examples

### Success — paid question
Inbound: "Hi, thanks! Is this paid or gifted?"
Reply: "Hi @alice — for this campaign, we usually work through
product gifting / barter. Would you be open to that?"
Facts: `offer.interest_clarify_asked=true` + `..._question=<text>`.

### Failure — already qualified
`goals.interest_qualification.status="satisfied"`. Skill aborts
`{"skipped":"already_qualified"}` so the dispatcher routes to the
next active goal instead.

## Pitfalls
- Two questions in one reply → KOL only answers the easier one and
  the lane stalls. Stick to ONE.
- Mentioning paid, hybrid, flexibility, budget, cash supplement, SKU, or
  deliverable counts contaminates downstream
  skill scope and removes their negotiation surface.
- Pre-committing `interest_signal=confirmed` on the basis of "love
  it!" alone — must wait for the KOL's actual confirmation reply.
- Asking a question the KOL already answered in `thread_history`.
  The whole point of the `[P0.3]` block is to prevent this loop —
  always scan it before composing the question.
- Forcing the lane forward when the KOL just asked a clarifying
  question on `current_goal`. `[P0.4]` rule 1 takes precedence over
  rule 2: answer first, advance only when the path is genuinely
  clear.
