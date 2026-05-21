---
name: kol-escalation-resumer
description: Resumes a previously-opened KOL escalation once the operator has answered. Reads the escalation record + operator answer + extracted operator_facts, then decides one of three branches — inject_and_continue (write facts + resume original goal), override_and_continue (patch campaign_config + resume), or escalate_again (re-open child escalation when answer is insufficient or attempts exhausted). Never sends mail directly. Writes only through the Bridge. Honors max_escalation_depth from policies/escalation_rules — at the threshold it forces escalate_again with `force_human_takeover_hint=true` rather than auto-aborting.
trigger: Invoked by `kol-reply-dispatcher` (or the escalation console resolve action) when an escalation transitions from `awaiting_answer` to `resolved`, or whenever the operator submits an answer through the console PATCH `/escalations/{id}` endpoint and the dispatcher needs to reconcile the parent goal. Never auto-runs without a resolved escalation row.
tags: ["kol", "escalation", "resume", "meta-lane", "policy-aware"]
---

## Goal
Take an operator-resolved escalation and translate the operator's
answer into deterministic CAL writes + a routing decision so the
parent goal can resume (or escalate further) without the operator
needing to remember which fact namespace it lives in.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Bridge is the only CAL writer.** Forbidden: direct cal.db, ad-hoc
  SQL, `execute_code`. All writes go through
  `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py` or HTTP routes.
- **Idempotent.** If the escalation is already `resolved` AND
  `resume_context.resumed_at` is set, abort with
  `{"skipped":"already_resumed"}`. Do NOT double-write facts.
- **Reads but never re-edits the escalation row.** Resolution itself
  is owned by the console (`PATCH /escalations/{id}`); this skill only
  consumes that resolution + writes downstream facts.
- **Depth-aware.** When `attempts_count >= max_escalation_depth`
  (default 3, configurable via `policies/escalation_rules` parsed
  metadata `max_escalation_depth: <n>`), force
  `decision = "escalate_again"` with
  `force_human_takeover_hint = true` — never auto-abort the goal.

## Inputs
1. `escalation_id` (mandatory).
2. `env` (`TEST` or `LIVE`, mandatory).
3. Optional `operator_summary` (free-text one-line note appended to
   the resume_context).

## Email Style Preamble (mandatory before drafting)

This skill **does not draft email by default** — it returns a
routing decision with `body: null`. However, when
`decision == "escalate_again"` AND a follow-up question to the KOL
is generated (rare), invoke `kol-email-style-loader` and prepend its
output verbatim to the LLM prompt. **P0 (goal / required facts) > P1
(company style) > P2 (personal style)**.

Call contract (only when drafting):
- inputs: `goal_brief = {goal: "escalation_resume", missing_facts: [<from parent goal_state>], next_action: "<one-line summary>"}`,
  `current_user_id = <operator id from session>`.
- failure mode: empty-doc fallbacks; never block.

>>> include: kol-email-style-loader

## Procedure

### Step 1 — Load escalation + parent context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-escalation \
  --escalation-id <escalation_id> --env <TEST|LIVE>
```
Read:
- `state` (must be `resolved`; if `awaiting_answer`, abort
  `{"skipped":"not_yet_resolved"}`).
- `decision` (`resume | terminate`). If `terminate`, jump to Step 4
  with `decision="terminate_goal"`.
- `operator_answer` (free-text), `operator_facts` (dict of
  `<namespace.key>: <value>`), `resume_context`, `parent_escalation_id`,
  `attempts_count`, `rule_id`, `identity_id`, `campaign_id`,
  `goal_name`, `lane`.

Also load:
- `goals.<goal_name>` from `get-dispatch-context` to know which facts
  remain `missing_facts`.
- `policies/escalation_rules/parsed` to read
  `max_escalation_depth` (default `3`).

### Step 2 — Branch the decision

Decision rules (first match wins):

| condition | decision |
|-----------|----------|
| `state != "resolved"` | abort, no decision |
| `decision_field == "terminate"` | `terminate_goal` |
| `attempts_count >= max_escalation_depth` AND missing_facts NOT fully covered by `operator_facts` | `escalate_again` + `force_human_takeover_hint=true` |
| `operator_facts` covers all `missing_facts` for `goal_name` | `inject_and_continue` |
| `operator_answer` contains an `override_config_patch:` block (operator approved a config change, e.g. `campaign_config.compensation_cap_usd=2000`) | `override_and_continue` |
| `operator_facts` partially covers missing_facts (some still missing) | `escalate_again` (child escalation) |
| else (empty or vague answer) | `escalate_again` |

`force_human_takeover_hint` is **only** a hint surfaced in the result
envelope and in the new escalation's `resume_context` — never
short-circuits the decision.

### Step 3 — Execute the branch

#### 3a. `inject_and_continue`
Write `operator_facts` via `write-facts-multi`, grouped by namespace:
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-escalation-resumer",
            "namespaces": <grouped operator_facts>}'
```
The facts must use the same namespace prefix as their key (e.g.
`offer.compensation_mode` goes under `"offer"`). On
`FactNamespaceError`, abort and surface the violation in the result.

After the write, the parent goal's `missing_facts` should clear; the
caller (`kol-reply-dispatcher`) re-runs `get-dispatch-context` to
confirm and dispatches the next sub-skill.

#### 3b. `override_and_continue`
Parse `operator_answer` for the `override_config_patch:` block. The
block is a YAML/JSON snippet identifying campaign-level overrides
(e.g. `compensation_cap_usd: 2000`, `gift_max_msrp_usd: 800`). Write
via:
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-campaign-config \
  --campaign-id "<campaign_id>" --env <TEST|LIVE> \
  --patch-json '<override_config_patch>'
```
Then write any incidental `operator_facts` from Step 3a.

#### 3c. `escalate_again`
Open a child escalation:
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py open-escalation \
  --identity-id <identity_id> --campaign-id "<campaign_id>" \
  --env <TEST|LIVE> \
  --json '{"rule_id":"<original rule_id>",
            "lane":"<lane>",
            "goal_name":"<goal_name>",
            "parent_escalation_id":<this escalation_id>,
            "question_to_operator":"<refined question with what is still missing>",
            "required_facts_to_resume":<remaining missing_facts>,
            "resume_context":{"force_human_takeover_hint":<bool>,
                                "previous_attempts":<attempts_count>,
                                "operator_summary":"<operator_summary or null>"}}'
```
The Bridge increments `attempts_count` on the parent automatically.
The new escalation's state is `awaiting_answer`.

#### 3d. `terminate_goal`
Mark `goal_name` as aborted by writing the goal's terminal facts:
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-escalation-resumer",
            "namespaces":{
              "approval":{"approval.<goal_name>_terminated":true,
                          "approval.<goal_name>_terminated_reason":"<from operator_answer>"}}}'
```
Goal recompute will mark the lane as aborted on next dispatch.

### Step 4 — Return envelope
Final assistant message must be a single JSON object:
```json
{
  "skill": "kol-escalation-resumer",
  "escalation_id": 17,
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "subject": null,
  "body": null,
  "decision": "inject_and_continue",
  "facts_written": {"offer": 2, "fulfillment": 0, "approval": 0},
  "override_config_patch": null,
  "child_escalation_id": null,
  "force_human_takeover_hint": false,
  "next_action": "Dispatcher should re-run get-dispatch-context and route the active goal."
}
```

`body` is always `null`. `subject` is always `null`. `decision` is
exactly one of:
`inject_and_continue | override_and_continue | escalate_again | terminate_goal`.

## Examples

### Inject and continue (happy path)
- Escalation rule_id=`compensation_cap_breach`,
  question="KOL asked $1800 — exceed cap of $1500. Approve?".
- Operator answer: "Yes, approve at $1800; mode=paid".
- `operator_facts` extracted: `{"offer.compensation_mode":"paid",
  "offer.agreed_terms":{"amount_usd":1800,"basis":"flat"}}`.
- Decision: `inject_and_continue`. Facts written. Dispatcher resumes
  `kol-compensation-negotiator` with `goals.compensation.status=satisfied`.

### Override and continue (rare, requires explicit operator block)
- Operator answer contains:
  ```
  override_config_patch:
    compensation_cap_usd: 2000
  ```
- Decision: `override_and_continue`. campaign_config patched. Goal
  resumes; future caps respected at $2000.

### Escalate again (depth threshold reached)
- Parent attempts_count=3, max_escalation_depth=3,
  operator_answer="not sure, ask CEO".
- Decision: `escalate_again` + `force_human_takeover_hint=true`.
  Child escalation opened with refined question and hint. Goal stays
  blocked but is **never auto-aborted**.

### Terminate
- Operator selects `terminate` in the console; `decision_field="terminate"`.
- Decision: `terminate_goal`. `approval.<goal>_terminated=true` written.
  Engagement aborts on next dispatch via `kol-archival-writer`.

## Pitfalls
- Auto-aborting at the depth threshold. **Never** — always escalate
  with the takeover hint. Aborts must be operator-initiated.
- Writing `operator_facts` without grouping by namespace prefix —
  triggers `FactNamespaceError`.
- Re-resolving a `decision="terminate"` escalation as
  `inject_and_continue` because `operator_facts` is populated. The
  decision field is authoritative; terminate wins.
- Forgetting `parent_escalation_id` on child escalations — breaks the
  attempts_count chain and the depth check.
- Drafting an email when the decision is anything other than
  `escalate_again` with a KOL-facing follow-up. Most resume paths are
  silent CAL writes; `body: null` is the default.
