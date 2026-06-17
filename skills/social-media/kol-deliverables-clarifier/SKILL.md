---
name: kol-deliverables-clarifier
description: Composes a reply when the KOL asks "what would you like me to do? / what's the deliverable / what's the budget?" — questions that conflate scope and price. This skill answers SCOPE only (platforms + count + usage rights) using `campaign_config.deliverable_platforms` / `deliverable_count_per_platform`, and explicitly defers price talk to the next exchange. Writes `offer.deliverable_platforms_proposed`, `offer.deliverable_count_proposed`, `offer.usage_rights_discussed=true` as appropriate.
trigger: Invoked by `kol-reply-dispatcher` when the classifier reports `active_goals_by_lane.commerce == "deliverables_scope"`. Typical inbound: "what's the deliverable count?" / "what platforms?" / "what's your usage rights ask?" / "what's your budget?" (the last one we deflect, not answer here).
tags: ["kol", "deliverables", "scope", "usage-rights", "draft-generator", "commerce-lane"]
---

## Goal
Lock the FRAMEWORK of the collab — platforms, count per platform,
usage rights — without committing to compensation. After this skill,
the dispatcher can advance to `compensation_negotiation`.

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
- **Answer scope. Defer price.** If KOL asks "what's your budget?"
  in the same email, acknowledge the question but say "let's
  align on scope first, then I'll come back with numbers" — do NOT
  quote a number.
- **Stay within `campaign_config` limits.** Do not propose more
  platforms or higher counts than configured.
- **Read stored deliverables spec (runtime, no NL parse).** When
  describing scope, include extras from
  `campaign_config.campaign_deliverables_json` (ad code, usage rights)
  via `GET /campaigns/{id}/resolved-deliverables` — do not invent ad
  code rows at reply time.
- **Open escalation when KOL pre-asks for over-cap.** If
  `inbound_excerpt` already implies "I'd want 5 IG + 5 TT" and that
  exceeds config, escalate instead of negotiating.
- **Idempotent.** If `goals.deliverables_scope.status == "satisfied"`,
  abort `{"skipped":"already_scoped"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `inbound_excerpt`.
3. `thread_history` (mandatory; may be `[]`) — JSON array of prior
   turns, oldest first, from `kol-reply-dispatcher` Step 0. Each
   entry: `{from, date, body}` only.
4. `flow_hint` — small JSON from dispatcher Step 5:
   `{lane, current_goal, next_goal_in_lane,
   missing_facts_for_current_goal, kol_signaled_next_step}`.
5. `learning_hints` — passed through by the dispatcher (reject few-shot +
   `reply_strategy` + `company_style`).

## Learning hints (advisory)

Read `learning_hints` per
`references/shared/learning-hints.md`. Apply
`reply_strategy` / `reply_learning` / `company_style` for `deliverables_scope`
as advisory wording/sequencing only. **Priority on conflict:** fact ownership
> pricing engine output > escalation gates > this skill's HARD rules
(`*_proposed` keys only, no price) > learning hints >
`references/learned/deliverables_scope.md` (if promoted) > CAL
`personalization_hint` > Hindsight recall (episodic) > MEMORY.md. When
`references/learned/deliverables_scope.md` exists, treat it as an
auto-promoted, advisory playbook under the same priority. Before drafting, if
`references/learned/deliverables_scope.md` exists, read it via `skill_view`.

## Email Style Preamble (mandatory before drafting)

Follow shared style-preamble baseline in
`references/shared/style-and-brief-preambles.md`.

Call contract:
- inputs: `goal_brief = {goal: "deliverables_scope", missing_facts: [<from goal_state>], next_action: "Communicate deliverable framework (no price)"}`,
  `current_user_id = <operator id from session>`.

>>> include: kol-email-style-loader

## Conversation History Preamble (mandatory before drafting)

After the style-loader block, prepend a `[P0.3] Conversation history`
section to the LLM prompt, built verbatim from the `thread_history`
input (oldest → newest, one entry per turn):

```
[P0.3] Conversation history so far (oldest first; latest_email is
shown separately under [INBOUND]):

— <from> · <date>
<body>

— <from> · <date>
<body>
...
```

When `thread_history` is `[]`, render
`[P0.3] Conversation history so far: (none).`

Hard rules attached to this block (include verbatim):

1. Do **not** re-propose a platform / count / usage-rights line that
   appears verbatim or near-verbatim in an earlier outbound turn
   above. Vary the phrasing; do not echo a prior pitch.
2. If the KOL has already named the platforms they care about in an
   earlier turn, lead with **those** in the scope sentence rather
   than restating the full whitelist.
3. Do **not** re-ask a deliverables sub-question the KOL already
   answered earlier in the thread (e.g. preferred platform mix).

## Flow Guidance Preamble (mandatory before drafting)

Immediately after `[P0.3]`, prepend a `[P0.4] Flow guidance` block
populated from `flow_hint`:

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

1. The standard lane order is a **default**, not a forced march.
   When `latest_email` asks a scope question, answer it on
   `current_goal` (this skill's job) and do not jump ahead.
2. When **all of** (a) `kol_signaled_next_step` is `true`,
   (b) the KOL did **not** raise a new scope question, and
   (c) `next_goal_in_lane` is non-null — the reply may close out
   scope and add a one-sentence soft handoff toward
   `next_goal_in_lane` (e.g. "once that's good, I'll come back with
   the comp side"). Do **not** start negotiating
   `next_goal_in_lane` in this same draft.
3. Never reopen a lane goal that is already satisfied unless the
   KOL explicitly reopens it.
4. If `[P0.3]` shows we already softly nudged toward
   `next_goal_in_lane` and the KOL didn't bite, do not push again
   here — keep the focus on scope.

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE> --view agent
```
Read:
- `campaign_config.deliverable_platforms` — required list.
- `campaign_config.deliverable_count_per_platform` — required int.
- `campaign_config.extra_notes` — sometimes contains usage-rights
  baseline (e.g. "30-day organic + paid usage").
- `goals.deliverables_scope.status` — must be `active`.

If platforms or count missing → abort
`{"error":"campaign_config_incomplete","missing":[...]}`.

When the dispatcher converts that abort into an escalation, it MUST
include the same list as a structured field in `resume_context` so the
operator UI can render it as chips (not just buried prose):

```
open-escalation --json '{"identity_id":...,"campaign_id":"...",
  "goal":"deliverables_scope",
  "reason":"campaign_config_incomplete_for_scope_reply",
  "question_to_operator":"...用简体中文列出缺失字段，操作员能直接看懂...",
  "resume_context":{"missing_config_fields":["deliverable_platforms",
                                              "deliverable_count_per_platform"]}}'
```

### Step 2 — Decide the response shape
**Branch A — KOL asked scope question:** propose framework explicitly:

> "For this collab we're looking at `<count>` post(s) per platform on
> `<platforms joined>`. On usage rights, `<extra_notes usage line or
> default '30-day organic only, no paid amplification without
> separate sign-off'>`. Does that work for you?"

**Branch B — KOL pre-asks for over-cap or extra platforms:**
e.g. "I'd want 5 IG reels + 3 TT + a YT mention". If any platform or
count exceeds config, do NOT counter-negotiate yourself; open an
escalation:
```
open-escalation --json '{"identity_id":...,"campaign_id":"...",
  "goal":"deliverables_scope",
  "reason":"KOL pre-asked over-cap: <excerpt>",
  "operator_note":"<inbound_excerpt>"}'
```
Return `{"escalation_opened": true}`.

**Branch C — KOL asked price ("what's your budget?"):** acknowledge
+ defer. Reply with the scope (Branch A body) and append:
> "Once we're aligned on the scope, I'll follow up with the comp side."
Do NOT quote any number. The comp turn is the next dispatcher pass.

### Step 3 — Write outbound facts
```
write-facts-multi --json '{
  "campaign_id":"...",
  "source":"skill:kol-deliverables-clarifier",
  "namespaces":{
    "offer": {"offer.deliverable_platforms_proposed": ["instagram","tiktok"],
               "offer.deliverable_count_proposed": 1,
               "offer.usage_rights_discussed": true}
  }
}'
```

We use `_proposed` because KOL hasn't agreed yet. The `*_proposed`
keys flip to non-prefixed `offer.deliverable_platforms` /
`offer.deliverable_count_per_platform` only when classifier on the
next inbound confirms agreement.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-deliverables-clarifier",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "body": "<reply>",
  "branch": "A_propose | B_escalated | C_defer_price",
  "facts_written": {"offer": <n>}
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft` (shared:
`references/shared/reply-envelope-contract.md`).

## Fragment mode (multi-goal dispatch)

When input includes `fragment_mode: true`:

- Run Steps 1–2 logic only; **do not** call `write-facts-multi` or
  `open-escalation`.
- **Do not** include greeting, sign-off, `to`, or `subject`.
- Return scope-only prose (platforms / count / usage rights — **no price**):

```json
{
  "fragment_mode": true,
  "goal": "deliverables_scope",
  "skill": "kol-deliverables-clarifier",
  "fragment": "<1-3 sentences: deliverable framework only>",
  "proposed_facts": {
    "offer.deliverable_platforms_proposed": ["instagram", "tiktok"],
    "offer.deliverable_count_proposed": 1,
    "offer.usage_rights_discussed": true
  },
  "branch": "A_propose | C_defer_price"
}
```

- **Branch B (over-cap):** return
  `{"fragment_mode": true, "gate": true, "reason": "...", "goal": "deliverables_scope"}`.
- `proposed_facts` keys MUST match `fact-ownership.md` for
  `deliverables_scope`.

## Examples

### Branch A
Inbound: "Sounds good — what's the deliverable count and platforms?"
Reply: "For this we're looking at 1 post per platform on Instagram +
TikTok. On usage rights, 30-day organic only, no paid without
separate sign-off. Does that work for you?"

### Branch C
Inbound: "What's your budget for 1 IG + 1 TT?"
Reply: scope paragraph + "Once we're aligned on scope, I'll follow up
on comp." NO number.

### Branch B
Inbound: "I'd need 3 IG reels and a YT mention."
config: platforms=[instagram,tiktok], count=1. → Escalation opened.

## Pitfalls
- Quoting a number to pre-empt the price question. Defer.
- Counter-offering ("ok 2 IG + 1 TT") on the spot when KOL asks
  over-cap. Escalate.
- Forgetting `usage_rights_discussed=true` — downstream
  `compensation_negotiator` checks this fact before quoting paid.
- Setting non-`_proposed` keys (`offer.deliverable_platforms`)
  before KOL agreement. Only the classifier on the next reply
  promotes `_proposed` → committed.
- Restating the same scope sentence the KOL already saw in
  `[P0.3]`. Vary the wording when re-confirming; never copy-paste a
  prior outbound paragraph.
- Jumping to comp talk because `kol_signaled_next_step=true` even
  though the KOL just asked a follow-up scope question. Answer
  scope first; one soft handoff sentence is the maximum forward
  motion allowed in a single reply.
