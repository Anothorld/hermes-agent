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

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Answer scope. Defer price.** If KOL asks "what's your budget?"
  in the same email, acknowledge the question but say "let's
  align on scope first, then I'll come back with numbers" — do NOT
  quote a number.
- **Stay within `campaign_config` limits.** Do not propose more
  platforms or higher counts than configured.
- **Open escalation when KOL pre-asks for over-cap.** If
  `inbound_excerpt` already implies "I'd want 5 IG + 5 TT" and that
  exceeds config, escalate instead of negotiating.
- **Idempotent.** If `goals.deliverables_scope.status == "satisfied"`,
  abort `{"skipped":"already_scoped"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `inbound_excerpt`.

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "deliverables_scope", missing_facts: [<from goal_state>], next_action: "Communicate deliverable framework (no price)"}`,
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
  "question_to_operator":"...names the missing fields in plain text...",
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
inbound message before persisting `approval.reply_draft`.

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
