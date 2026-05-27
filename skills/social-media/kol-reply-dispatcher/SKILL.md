---
name: kol-reply-dispatcher
description: Cron-triggered KOL reply router (formerly intent-routing dispatcher; now goal-state aware). Every 10 minutes, pulls unread Gmail replies plus TEST-mode self-replies, calls `kol-email-stage-classifier` to extract multi-namespace facts and per-lane active goals, writes those facts to the Bridge so goal_state recomputes, then for each lane independently decides next-action by dynamic priority (default commerce > fulfillment > publish; severity-gated reversal allowed). Picks the highest-priority unblocked lane as primary author and degrades the others to side-topics or `approval.pending_topics`. Drafts NO email here; delegates to the chosen child skill or opens an escalation. Never sends; never auto-decides budget; never bypasses Bridge for CAL writes.
trigger: Runs on cron `*/10 * * * *` under profile `outreach-operator`. Also runs on demand when the user types "check KOL replies", "process inbound replies", or "route latest KOL email".
tags: ["kol", "outreach", "router", "reply", "cron", "gmail", "goal-state", "lanes"]
---

## Goal
Keep KOL outreach moving by classifying each new inbound email, persisting
its facts so goal_state recomputes, and selecting the next child skill (or
opening an escalation) per lane — without sending mail, without writing CAL
directly, and without making a business decision the goal-state machine
should make.

## Runtime Contract
- Frequency: every 10 minutes via Hermes `cronjob`. Profile:
  `outreach-operator`.
- Cron pre-run: a minimal context collector (Phase B replacement for the
  legacy `kol_reply_dispatcher.py` script) reports a `pending_replies`
  array. If absent / empty, exit immediately. Each item must carry: matched
  `identity_id`, `campaign_id`, `env`, the raw email, the thread summary,
  and the dispatch-context snapshot (see Step 1). (Until that script lands
  in a later phase, the agent may invoke this skill on-demand via chat with
  one email at a time; do **not** auto-sweep Gmail from the LLM directly.)
- **Bridge is the only CAL writer/reader.** Forbidden: import `cal.py`,
  open `~/.hermes/kol-ops-bridge/cal.db`, ad-hoc SQL, `execute_code`. Use
  `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py` (deterministic CLI)
  or HTTP endpoints under `/api/plugins/kol-ops-bridge/`. Always pass
  `--env <TEST|LIVE>`; never let it default.
- **Drafts no email and never sends.** Drafting is delegated to a child
  skill; sending requires explicit human action in the Gmail Drafts inbox.
- **Idempotency:** a message is processed at most once. The Gmail label
  flow (`kol-outreach/pending-reply` → `kol-outreach/handled`) is the
  authority; the classifier output is informational, not a state machine.
- **Hard stop:** if `campaign_config` is missing or `goals` cannot be
  fetched for any reply, open an escalation for that thread with reason
  `dispatcher_missing_context` and continue with the rest. **Never invoke
  a drafting child skill without goal_state.**
- The legacy 9-class intent-routing table is **gone**. Routing is by the
  server-side goal_state, not by intent.

## Inputs
1. `pending_replies[]` — see Cron pre-run above.
2. (Implicit) operator chat context if invoked on demand.

## Procedure

### Step 1 — Fetch dispatch context (one call)
For each `pending_replies[i]`, fetch the bundled context:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```

Response: `{goals, lanes, relationship, reusable_facts, campaign_config}`. This **replaces**
the legacy `get-goals` + `get-relationship` + `get-reusable-facts` +
`get-lanes` chain — do not call those individually.

### Step 2 — Run the classifier
Invoke `kol-email-stage-classifier` with `latest_email`, `thread_summary`,
`current_goal_state` (from Step 1's `goals`), `campaign_config_summary`,
and (if applicable) `relationship_summary` (from Step 1's `relationship`
+ `reusable_facts` + `campaign_config`). The classifier returns the JSON shape defined in its
SKILL.md. **Do not paraphrase or modify** its output.

### Step 3 — Persist extracted facts (one call across all namespaces)
Write every non-empty namespace from `facts_extracted` in a single call:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"email:<message_id>",
            "namespaces":{
              "offer":       {"offer.<key>": <val>, ...},
              "identity":    {"identity.<key>": <val>, ...},
              "fulfillment": {"fulfillment.<key>": <val>, ...},
              "approval":    {"approval.<key>": <val>, ...}
            }}'
```

- Empty namespaces may be omitted; the Bridge no-ops them.
- Each fact key MUST be dotted-prefix; the Bridge enforces this with
  `FactNamespaceError` and **rejects the whole call** before any insert if
  any key is malformed. If you hit one, abort that reply, open an
  escalation with reason `fact_namespace_violation`, log raw classifier
  output, and move on. Do **not** retry with munged keys.
- After the write, re-fetch dispatch context with `get-dispatch-context`.
  This is the **server's** view of which goals are now active / satisfied
  / blocked, and supersedes the classifier's `active_goals_by_lane`.

### Step 3.5 — Honor classifier `escalation_hint`
The classifier's output may include an `escalation_hint` block. When
`escalation_hint.should_consider == true` for the lane the classifier
flagged, **immediately open an escalation for that lane and skip
drafting** — do not invoke a child skill for that lane in Step 4/5:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py open-escalation \
  --identity-id <identity_id> --campaign-id "<campaign_id>" \
  --env <TEST|LIVE> \
  --json '{"rule_id": "<escalation_hint.matched_rule_id>",
            "lane": "<lane>",
            "goal_name": "<active goal in that lane>",
            "severity": "<rule severity, default normal>",
            "question_to_operator": "<escalation_hint.suggested_question>",
            "required_facts_to_resume": <escalation_hint.required_facts_to_resume>,
            "resume_context": {"matched_rule_id": "<id>",
                                 "source": "classifier"}}'
```

Notes:
- The Bridge automatically tags `force_human_takeover_hint=true` in
  `resume_context` when the new escalation's `attempts_count` reaches
  `max_escalation_depth` (parsed from `policies/escalation_rules`,
  default `3`). **Never auto-abort** the goal — the depth-hit case
  still escalates to a human.
- Fallback: if the `escalation_rules` policy is missing or the
  classifier was invoked without it, `escalation_hint.should_consider`
  is implicitly `false` and this step is a no-op.
- A lane that opened an escalation here **must not** also be picked
  as primary author in Step 5; it is treated as `blocked`.

### Step 4 — Per-lane next-action decision
For each lane in `{commerce, fulfillment, publish, meta}`, given the
server-side goal_state from Step 3's re-fetch:

| Server goal status | Lane action |
|---|---|
| `satisfied` | No next action; lane idle. |
| `blocked` (has `blocking_escalation_id`) | No next action; the open escalation must resolve first. |
| `skipped` | No next action. |
| `aborted` | No next action; KOL is dead in this lane. |
| `active`, no human gates triggered | Pick the child skill bound to that goal (table below). |
| `active`, human gates triggered | Open an escalation; do NOT invoke a drafting skill. |

Goal → child skill:

| Goal | Child skill |
|---|---|
| `cold_outreach` | `kol-cold-outreach` |
| `reengagement_outreach` | `kol-reengagement-outreach` |
| `interest_qualification` | `kol-interest-qualifier` |
| `product_selection` | `kol-product-selector` |
| `deliverables_scope` | `kol-deliverables-clarifier` |
| `compensation_negotiation` | `kol-compensation-negotiator` |
| `contract_signing` | `kol-contract-coordinator` |
| `logistics` (`address_collected` missing) | `kol-shipping-intake` |
| `logistics` (post-address) | `kol-logistics-tracker` |
| `payout_setup` | `kol-payout-method-intake` |
| `content_production` (no `brief_sent`) | `kol-brief-sender` |
| `content_production` (`brief_sent` true, no `draft_submitted`) | (wait; no draft yet) |
| `content_review_and_golive` | `kol-content-reviewer` then `kol-golive-and-boost` |
| `post_collab_archival` | `kol-archival-writer` |

Many of these child skills land in later Phase B sub-phases. If the chosen
skill is not yet present, write an `approval.pending_action_<goal>` fact
(via `write-facts-multi` from Step 3 or a follow-up call) so an operator
can pick it up.

### Step 5 — Lane priority and primary author selection
1. Default priority: `commerce > fulfillment > publish > meta`.
2. **Severity reversal:** if any `fulfillment` or `publish` action carries a
   `severity ∈ {critical, blocking}` from the classifier's `signals` (e.g.
   `not_received`, `address_questioned`, `rejects_revisions`,
   `escalation_pattern_match:*`), it temporarily outranks `commerce`.
3. Pick the **highest-priority lane that is not blocked/idle** as the
   primary lane. Invoke its child skill with the full reply context.
4. For non-primary lanes that have a next action, do NOT invoke their
   skill. Instead append to the same `write-facts-multi` payload (or issue
   one follow-up call) under `approval`:
   ```
   "approval.pending_topics": ["<lane>:<goal>:<one-line topic>", ...]
   ```

### Step 5.5 — Persist draft or escalation outcome
If Step 5 invoked a child skill and the child returns a draft envelope,
persist it before reporting success. The dispatcher itself still does not
send mail; it creates an operator-review artifact in CAL.

**Envelope enrichment (mandatory).** Reply-side child skills return
content only — they do not know the recipient or subject. Before writing
the fact, build a `<merged draft envelope>` from the child's envelope
plus the inbound `latest_email` you already have in Step 1's
`pending_replies` payload:

- `to` ← `latest_email.from`
- `subject` ← `latest_email.subject` (prefixed with `Re: ` unless it
  already starts with `Re:` / `re:` / `RE:`)
- `body`, `thread_id`, and any other fields keep the child's value

Use `<merged draft envelope>` (not `<child draft envelope>`) in both
writes below.

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-event \
  --identity-id <identity_id> --campaign-id "<campaign_id>" \
  --env <TEST|LIVE> --event-type kol_reply_draft_ready \
  --actor agent:kol-reply-dispatcher \
  --json '{"payload":{"source_message_id":"<inbound_message_id>",
                       "primary_lane":"<lane>",
                       "primary_goal":"<goal>",
                       "child_skill":"<skill>",
                       "draft":<merged draft envelope>}}'
```

Then write one approval fact so the web console / operator queue can find
the draft even if the agent transcript is later compacted:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"draft:<inbound_message_id>",
            "namespaces":{"approval":{
              "approval.reply_draft":{
                "decision":"pending",
                "source_message_id":"<inbound_message_id>",
                "primary_lane":"<lane>",
                "primary_goal":"<goal>",
                "child_skill":"<skill>",
                "draft":<merged draft envelope>
              }}}}'
```

The bridge rejects an `approval.reply_draft` whose `draft` is missing
non-empty `subject` / `body` / `to` — if write-facts-multi 400s with
`approval.reply_draft.draft missing/empty: ...`, your enrichment step
did not run.

If the selected child skill cannot produce a draft because required facts
are missing, open an escalation instead of returning a free-text failure.
When the child returned `{"error":"campaign_config_incomplete","missing":[...]}`
(or any other structured missing-fact signal), the escalation MUST forward
that list as `resume_context.missing_config_fields` so the operator UI
can render it as chips. Example:

```
open-escalation --json '{"identity_id":...,"campaign_id":"...",
  "goal":"<active goal>",
  "reason":"campaign_config_incomplete_for_<lane>_reply",
  "question_to_operator":"Campaign config is incomplete for <goal>: <fields> are empty/null. <context>",
  "resume_context":{"missing_config_fields":["deliverable_platforms",
                                              "deliverable_count_per_platform"],
                     "source":"child_skill_abort",
                     "child_skill":"<skill name>"}}'
```

Every processed reply must end in exactly one durable outcome:
`kol_reply_draft_ready`, `open-escalation`, or `approval.pending_action_*`.

### Step 5.6 — Refinement runs (operator-triggered regeneration)
When the brief is an `approval_refine` (operator clicked 优化/重新生成
on the Approvals page), the input carries `operator_refinement_prompt`
and the full prior `approval.reply_draft` value under
`current_value_json`. In that mode:

- Skip Steps 1–4 (no classification, no fact-write, no skill selection).
  Re-invoke **the same** `child_skill` named in `current_value_json`.
- Pass through the original inbound context (recover from
  `kol_inbound_reply` events for `source_message_id` if needed) **plus**
  the `operator_refinement_prompt` as an extra input field. The child
  skill treats it as a hard constraint on the new draft's *content*
  (tone, additions, removals) and must still return the same envelope
  shape (`Step 5 — Return draft envelope`).
- Do **not** rewrite `offer.*` or any other domain facts on a refinement
  run — it is content-only.
- Apply the **same envelope enrichment as Step 5.5** to the child's new
  envelope: fill `to` and `subject` from the recovered inbound
  `kol_inbound_reply` event (`from_addr` and `Re: <subject>`). The
  bridge's write-time validator will reject a sparse `draft` here too.
- Persist the result by rewriting the same `approval.reply_draft` fact
  via `write-facts-multi`. The new value MUST keep `decision="pending"`,
  `source_message_id`, `primary_lane`, `primary_goal`, `child_skill`,
  and `linked_escalation_id`; set `draft` to the new **merged** envelope;
  prepend the prior `draft` into `previous_drafts` (cap 5); and append
  `{prompt, at, by}` to `refinement_history` (cap 5).
- Do **not** open a new escalation, do **not** send mail, do **not**
  create a Gmail draft. Skip Step 6.

### Step 6 — Idempotency labels
After Step 5.5 (or an escalation was opened), apply the Gmail label
`kol-outreach/handled` to that message and remove
`kol-outreach/pending-reply`. The Gmail label transition is the only
state-machine for "have we processed this email"; do **not** rely on CAL
events for re-entry detection.

### Step 7 — Final report
Return a JSON summary covering each processed reply:

```json
[
  {
    "identity_id": 42,
    "campaign_id": "TS8319",
    "env": "TEST",
    "primary_lane": "commerce",
    "primary_goal": "compensation_negotiation",
    "primary_skill_invoked": "kol-compensation-negotiator",
    "side_topics": ["fulfillment:logistics:address still pending"],
    "draft_event_written": true,
    "approval_fact_written": "approval.reply_draft",
    "escalation_opened": null
  },
  ...
]
```

## Examples

### Success — single-lane
KOL replies "I'd love to collaborate, what's the budget?". Step 1 returns
`outreach.satisfied`. Classifier emits
`facts_extracted.offer={"offer.interest_signal":"confirmed"}`. Step 3 writes
that one namespace via `write-facts-multi`. Server re-fetch shows
`deliverables_scope` active in commerce. Primary author =
`kol-deliverables-clarifier`.

### Success — multi-lane, severity reversal, single fact write
KOL replies with both "the package never arrived" and "we should talk price
again". Classifier emits `facts_extracted.offer={...}` plus
`facts_extracted.fulfillment={...}` plus `signals=[{name:"not_received",
severity:"critical"}, ...]`. Step 3's `write-facts-multi` writes BOTH
namespaces in one call. Re-fetch shows `compensation_negotiation` active
(commerce) and `logistics` active (fulfillment). Severity reversal →
primary lane = fulfillment, primary skill = `kol-logistics-tracker`.
Commerce side-topic written via a second `write-facts-multi` call:
```
"approval.pending_topics":
  ["commerce:compensation_negotiation:KOL re-opened price; defer until package located"]
```

### Failure — namespace violation
Classifier emits `compensation_mode` (no prefix). Step 3 hits
`FactNamespaceError` on the whole call (atomic — nothing is written). Open
escalation with reason `fact_namespace_violation`, log raw classifier
output, skip drafting.

### Failure — missing config
`campaign_config` not in the snapshot. Step 1's `get-dispatch-context`
returns 404. Open escalation `dispatcher_missing_context`. Do NOT proceed.

## Pitfalls
- The classifier's `active_goals_by_lane` is a **hint**, not the truth.
  Always re-fetch `get-dispatch-context` after writing facts and trust the
  server.
- Side-topics only via `approval.pending_topics` — never silently drop a
  non-primary-lane action.
- A reply that fits **no** active goal still needs a label transition;
  mark `kol-outreach/handled` and add an `approval.unmatched_reply` fact
  so the operator notices.
- `write-facts-multi` is atomic on validation: a single bad key blocks the
  whole batch. Treat it as transactional and don't try to "salvage" valid
  namespaces by retrying piecemeal — fix the classifier output instead.
- The legacy 9-class intent table is no longer authoritative; if a SKILL.md
  elsewhere references it, treat that reference as stale documentation
  pending Phase B cleanup.
- Bridge open mode (no `X-Bridge-Key`) silently allows mutation but logs a
  WARN; in production cron you must set `HERMES_KOL_OPS_BRIDGE_KEY` so a
  rotation incident doesn't go unnoticed.
