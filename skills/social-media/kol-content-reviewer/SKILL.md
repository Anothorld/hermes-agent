---
name: kol-content-reviewer
description: Reviews the KOL's submitted draft (link or attachment) against `campaign_config.audit_standards_md`. Three branches — (A) approve: standards met → write `fulfillment.draft_approved=true`; (B) request_revision: minor/scoped issues → draft itemized feedback list and write `fulfillment.revision_requested_count++`; (C) escalate: major brand-safety / off-policy / >2 revision rounds → open escalation. Side effect of approval is to enable `golive-and-boost`.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.publish == "content_review"` AND `fulfillment.draft_submitted == true` AND `fulfillment.draft_approved != true`.
tags: ["kol", "content-review", "draft-generator", "publish-lane"]
---

## Goal
Compare submitted draft against the configured audit standards and
either approve, request a bounded revision, or escalate. Never make
substantive creative judgments outside `audit_standards_md`.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Standards in, decisions out.** Every revision item must cite a
  specific clause of `audit_standards_md` (or a contract clause).
  Items not grounded in standards must NOT be requested.
- **Bounded revision rounds.** After `fulfillment.revision_requested_count >= 2`
  the next pass MUST escalate, not request a third round.
- **No clause invention.** If the draft violates something not covered
  by `audit_standards_md`, escalate with `goal=content_review reason="off-policy issue not covered by audit standards: <excerpt>"`.
- **Idempotent.** If `fulfillment.draft_approved==true`, abort
  `{"skipped":"already_approved"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. Classifier-extracted `fulfillment.draft_url` and/or `fulfillment.draft_excerpt`.
3. Operator-supplied `review_findings` (ordered list of
   `{clause_id, severity, excerpt, suggestion}`) — the calling layer
   (web console or LLM scaffolding) does the actual reading; this skill
   composes the email and writes the facts.

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "content_review_and_golive", missing_facts: [<from goal_state>], next_action: "<approve / request revision / escalate>"}`,
  `current_user_id = <operator id from session>`.
- output: prepend as the first section of the draft prompt; verbatim audit
  standards from `campaign_config.audit_standards_md` are P0 content.
- failure mode: empty-doc fallbacks; never block.

>>> include: kol-email-style-loader

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `campaign_config.audit_standards_md` — required.
- `goals.content_review.status` — must be `active`.
- Latest `fulfillment.revision_requested_count` (default 0).
- Latest `fulfillment.draft_url` (snapshot from classifier).

### Step 2 — Branch on `review_findings`

| Conditions | Branch |
|---|---|
| `review_findings` empty AND no major issues | A — approve |
| All items have `severity ∈ {minor, scoped}` AND `revision_requested_count < 2` | B — request_revision |
| Any `severity == major` (brand-safety / contractual / claim risk) | C — escalate |
| `severity == minor` but `revision_requested_count >= 2` | C — escalate (cap reached) |
| Any item not citing a clause from `audit_standards_md` or contract | C — escalate |

### Step 3 — Compose email + write facts

**Branch A — approve:**
- Body: "Looks great — you're cleared to post! Quick reminder: `<one
  line of golive logistics — handle/hashtag will follow in the next
  message>`. Thanks for the quick turnaround."
- Write:
  ```
  "publish": {"fulfillment.draft_approved": true,
              "fulfillment.draft_approved_at": "<iso8601>",
              "fulfillment.draft_url_at_approval": "<url>"}
  ```

**Branch B — request_revision:**
- Body skeleton:
  > "Took a look — really close. A couple of tweaks before we lock it in:
  > 1. `<excerpt → suggestion>` (per `<clause_id>`)
  > 2. ...
  > Could you ping me with the revised version? Aim to keep
  > everything else as-is."
- One bullet per finding. Cite clause id inline.
- Write:
  ```
  "publish": {"fulfillment.revision_requested_count": <prev+1>,
              "fulfillment.revision_requested_at": "<iso8601>",
              "fulfillment.last_revision_findings": <findings json>}
  ```

**Branch C — escalate:**
- Open escalation with grounded reason.
- Write `approval.content_review_escalation={"kind":"<major|cap_reached|off_policy>","findings":<...>,"decision":"pending"}`.
- `body: null` — escalation resumer drafts the response.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-content-reviewer",
  "branch_action": "approved | revision_requested | escalated",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": null,
  "body": "<reply or null>",
  "facts_written": {"publish": <n>, "approval": <n>},
  "escalation_opened": false,
  "revision_round_after": <int>
}
```

## Examples

### Approved
`review_findings=[]`. Branch A writes `draft_approved=true` + url
snapshot, drafts the cleared-to-post email.

### Revision (round 1)
Findings: `[{clause_id:"audit.disclosure", severity:"minor", excerpt:"missing #ad",
suggestion:"add #ad in first sentence"}]`. Branch B drafts numbered list,
writes `revision_requested_count=1`.

### Cap reached
`revision_requested_count=2`, new minor finding. Branch C escalates
with `kind=cap_reached`.

## Pitfalls
- Listing creative preferences not in `audit_standards_md` — those
  are operator-only (escalation, not skill territory).
- Granting "soft approval" with conditions ("approved if you also..."):
  always either approve OR request_revision; no half-approve.
- Stopping `revision_requested_count` from incrementing → infinite
  loop. Always +1 on Branch B.
- Forgetting to snapshot `draft_url_at_approval` so we know which URL
  was approved when KOL changes the post post-launch.
