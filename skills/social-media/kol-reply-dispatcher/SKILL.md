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

## Shared Blocks (Phase 3)
- Bridge runtime core:
  `references/shared/bridge-runtime-core.md`
- Inbound poller / pre-run contract:
  `references/shared/inbound-poller-runtime.md`
- Router/dispatcher boundaries:
  `references/shared/router-dispatcher-boundaries.md`
- Fact ownership (fragment mode):
  `references/shared/fact-ownership.md`
- Bridge endpoints (CLI names — **use this, not missing paths**):
  `references/shared/bridge-http-api-endpoints.md`
- Runtime learning hints (reject few-shot + personalization):
  `references/shared/learning-hints.md`

## Runtime Contract
- Frequency: every 10 minutes via Hermes `cronjob`. Profile:
  `outreach-operator`.
- Cron pre-run: the bridge **`inbound_reply`** module (worker in `serve.py`, or
  standalone `scripts/kol_reply_dispatcher.py` for HTTP debugging only) collects
  unread Gmail and launches gateway runs with a `pending_replies` array. If your
  run input has no `pending_replies` / it is empty, exit immediately. Each item
  must include `identity_id`, `campaign_id`, `env`, the raw `latest_email`, the
  `thread_history` (lean list of prior turns; see Step 0 below),
  deterministic `anomaly_signals` (thread/identity/risk soft-controls; see
  Step 0b below), `chase_context`, and the dispatch-context snapshot (see Step 1).
  Full poller contract:
  `references/shared/inbound-poller-runtime.md`.
  On-demand chat runs (one email at a time) are allowed; do **not** auto-sweep
  Gmail from the LLM directly.
- Follow shared bridge runtime core:
  `references/shared/bridge-runtime-core.md`.
- Follow shared router/dispatcher boundaries:
  `references/shared/router-dispatcher-boundaries.md`.
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

### Step 0 — `thread_history` shape (read-only, supplied by pre-run)

`pending_replies[i].thread_history` is the **prior** turns of the Gmail
thread (oldest first), excluding `latest_email`. Each entry is the lean
shape:

```json
{ "from": "alice@example.com",
   "date": "Mon, 5 May 2026 14:02:11 -0700",
   "body": "<the message body, clipped>" }
```

Only `from`, `date`, `body` are present — no headers, no message_id, no
subject, no thread_id, no snippet. Per-message body is clipped to ~4k
chars and the whole list to ~24k chars; an extra trailing entry with an
empty `from` and a body starting with `... [history truncated:` signals
that earlier turns were dropped. When the thread has no prior turn
(first inbound after our cold open), `thread_history` is `[]`.

**Do not** rewrap, paraphrase, or "summarize" `thread_history` before
forwarding it to the classifier or child skills — pass it through
verbatim. The whole point is to give downstream LLMs the actual words
both sides used, so they don't re-ask questions that were already
answered and don't repeat phrasing the KOL has already seen.

### Step 0b — `anomaly_signals` shape (read-only, supplied by pre-run)

`pending_replies[i].anomaly_signals` provides deterministic integrity and
soft-gating hints from the pre-run matcher:

```json
{
  "thread_integrity": {"status":"strict|weak|detached", "matched_by":"in_reply_to|thread_id|heuristic|none"},
  "identity_integrity": {"status":"matched|drifted|delegated|unknown", "sender_email":"...", "expected_email":"...", "reasons":[...]},
  "content_risk": "c1|c2|c3",
  "risk_controls": {"allow_autoflow": true, "gate_budget": false, "gate_contract": false, "gate_payout": false}
}
```

Treat these as baseline controls:
- `allow_autoflow=false` means this reply should not continue normal
  auto-negotiation flow in this turn.
- `gate_*` flags indicate sensitive dimensions requiring human gate even when
  the rest of the flow can continue.

When `anomaly_signals.mailbox_mismatch` is true, the KOL reply landed in a
**different operator Gmail inbox** than the campaign's bound outbound mailbox
(`bound_mailbox_email` vs `detected_mailbox_email`). The **poller already
writes the inbound event, opens escalation `inbound_mailbox_mismatch`, and
skips auto-dispatch** — your on-demand run should treat this as
escalation-only: resolve the mailbox mismatch with the operator before
drafting.

If you see `mailbox_mismatch` without an open escalation (legacy replay),
open escalation `inbound_mailbox_mismatch` with both emails in
`resume_context` and **do not** auto-draft.

Each `pending_replies[i]` item also carries `detected_mailbox_user_id` and
`detected_mailbox_email` (the inbox that received this message). Pass these
through to Step 6 label calls.

### Step 0c — `chase_context` shape (read-only, supplied by pre-run)

`pending_replies[i].chase_context` is computed deterministically by the
poller from the latest ``approval.reply_draft`` fact + inbound ids:

```json
{
  "prior_pending_draft": true,
  "prior_approved_unsent": false,
  "prior_source_message_id": "19e84b2d4cf91067",
  "prior_thread_id": "19e81ff6def3b65f",
  "recommended_action": "regenerate",
  "stale_hours": 36.2,
  "inbound_message_id": "19e8ef255f17f7af",
  "inbound_thread_id": "19e81ff6def3b65f"
}
```

`recommended_action` values:

| Value | Meaning |
|-------|---------|
| `proceed_normal` | No stale draft blocking this turn |
| `skip_same_source` | Draft already targets this inbound message |
| `regenerate` | **Follow-up chase** — supersede stale draft for prior message |
| `escalate_thread_fork` | Prior draft thread ≠ inbound thread with no CAL link — escalate only |

Optional probe (same logic): `get-reply-chase-hint --identity-id ID --campaign-id CID --message-id MID --thread-id TH --env LIVE`.

### Step 1 — Fetch dispatch context (one call)
For each `pending_replies[i]`, fetch the bundled context:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```

Response: `{goals, lanes, relationship, reusable_facts, campaign_config,
campaign_facts, identity_facts, candidate, learning_hints}`. This **replaces**
the legacy `get-goals` + `get-relationship` + `get-reusable-facts` +
`get-lanes` chain — do not call those individually.

Pass `learning_hints` and `reusable_facts.facts.personalization_hint` through
to every drafting child skill (see `references/shared/learning-hints.md`).

Use `campaign_facts` for per-campaign negotiation state (`offer.barter_attempted`,
`offer.rate_requested`, `offer.proposed_amount`, …). Optional narrow read:
`get-facts --identity-id ID --campaign-id CID --env LIVE`.

### Step 2 — Run the classifier
Invoke `kol-email-stage-classifier` with `latest_email`, `thread_history`
(verbatim from Step 0), `anomaly_signals` (from Step 0b),
`current_goal_state` (from Step 1's `goals`),
`campaign_facts` (from Step 1's `campaign_facts`),
`campaign_config_summary`, and (if applicable) `relationship_summary`
(from Step 1's `relationship` + `reusable_facts` + `campaign_config`).
The classifier returns the JSON shape defined in its SKILL.md.
**Do not paraphrase or modify** its output.

### Step 3 — Persist extracted facts (one call across all namespaces)
Write every non-empty namespace from `facts_extracted` in a single call:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"email:<message_id>",
            "signals": <classifier signals array verbatim>,
            "namespaces":{
              "offer":       {"offer.<key>": <val>, ...},
              "identity":    {"identity.<key>": <val>, ...},
              "fulfillment": {"fulfillment.<key>": <val>, ...},
              "approval":    {"approval.<key>": <val>, ...}
            }}'
```

- **Mandatory:** pass the classifier's `signals` array in the same JSON body.
  When `source` is `email:<message_id>`, the Bridge deterministically
  rewrites/drops premature **committed** keys (e.g. `offer.interest_signal=confirmed`
  on inquiry-only inbound, `offer.deliverable_platforms` → `*_proposed`) before
  insert. The response may include `classifier_sanitize` with an audit trail.
- Empty namespaces may be omitted; the Bridge no-ops them.
- Each fact key MUST be dotted-prefix; the Bridge enforces this with
  `FactNamespaceError` and **rejects the whole call** before any insert if
  any key is malformed. If you hit one, abort that reply, open an
  escalation with reason `fact_namespace_violation`, log raw classifier
  output, and move on. Do **not** retry with munged keys.
- After the write, re-fetch dispatch context with `get-dispatch-context`.
  This is the **server's** view of which goals are now active / satisfied
  / blocked, and supersedes the classifier's `active_goals_by_lane`.

### Step 3.1 — Chase supersede (mandatory when `chase_context.recommended_action == "regenerate"`)

Read `pending_replies[i].chase_context` **after** Step 3 re-fetch.

When `recommended_action == "defer_escalation"` (open escalation
`awaiting_answer` on this identity+campaign — see Bridge `reply_chase_hint`):

- **Do not** `persist-reply-draft` this turn (no chase placeholder while the
  operator is answering the escalation). The Bridge also returns **409**
  `open_escalation_awaiting_answer` if you try without `linked_escalation_id`.
- Confirm the escalation is already open (Step 3.5 or an existing row); do
  not open a duplicate.
- The Bridge **automatically** appends this inbound to the open escalation's
  `resume_context.pending_inbounds` and `question_to_operator` when the
  poller writes `kol_inbound_reply` via `POST /events` — no CLI step needed.
- Skip Steps 4–5 drafting; jump to Step 6 and mark the inbound handled.

When `recommended_action == "regenerate"` **and** `allow_autoflow` is still
true (Step 3.25 did not block this turn) **and** Step 3.5 did not open a new
escalation this turn:

1. **Do not** write `approval.pending_action_reply_needed` / `pending_action_reason`
   as the sole outcome — the Bridge rejects these on follow-up inbounds.
2. Continue Steps 4–5.6 normally (classifier facts already written in Step 3).
3. The merged reply body **must** open with a one-sentence acknowledgment of
   the follow-up (e.g. “Thanks for following up — …”) before restating the offer.
4. **Must** persist via `persist-reply-draft` with
   `source_message_id = latest_email.message_id` (not the prior draft's id).
   The Bridge writes `kol_reply_draft_superseded` and tags the new fact with
   `chase_supersede` for the operator console.
5. Reuse any open linked escalation; do not open a duplicate for the same chase.

When `recommended_action == "escalate_thread_fork"`:

- Open escalation `reply_thread_fork` with both thread ids in `resume_context`.
- Do **not** auto-draft this turn.

When `proceed_normal` or `skip_same_source`: no extra chase handling.

### Step 3.25 — Soft-control anomaly gating (mandatory)

Read effective controls from classifier output `risk_controls` (fallback to
Step 0b `anomaly_signals.risk_controls` when missing):

1. If `allow_autoflow == false`:
   - Open escalation `reply_identity_or_thread_anomaly`.
   - Do **not** invoke a normal business child skill this turn.
   - Write `approval.pending_topics += ["meta:identity_verification:confirm sender authority before continuing"]`.
2. If `allow_autoflow == true` but any `gate_* == true`:
   - Continue non-sensitive progression.
   - For sensitive goals, force human gate (no direct drafting):
     - `gate_budget=true` blocks compensation quoting/countering **except**
       when `identity_integrity.status == "delegated"` and the only gate is
       budget (agency/manager rate discussion on-scope) — the poller already
       clears `gate_budget` in that case; honor it and allow
       `compensation_negotiation` to draft.
     - `gate_contract=true` blocks contract-term acceptance/changes.
     - `gate_payout=true` blocks payout method/account changes.
   - Surface this as `approval.pending_topics` entries so operators can
     resolve without losing lane context.

### Step 3.5 — Honor classifier `escalation_hint`
The classifier's output may include an `escalation_hint` block. When
`escalation_hint.should_consider == true` for the lane the classifier
flagged, **immediately open an escalation for that lane and skip
drafting** — do not invoke a child skill for that lane in Step 4/5:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py open-escalation \
  --identity-id <identity_id> --campaign-id "<campaign_id>" \
  --env <TEST|LIVE> \
  --json '{"reason": "<escalation_hint.matched_rule_id or rule_id>",
            "lane": "<lane>",
            "goal_name": "<active goal in that lane>",
            "severity": "<rule severity, default normal>",
            "question_to_operator": "<escalation_hint.suggested_question>",
            "required_facts_to_resume": <escalation_hint.required_facts_to_resume>,
            "resume_context": {"matched_rule_id": "<id>",
                                 "source": "classifier",
                                 "source_message_id": "<latest_email.message_id>",
                                 "thread_id": "<latest_email.thread_id if present>"}}'
```

(`reason` is required; `rule_id` alone is accepted as an alias. `--identity-id`
/ `--campaign-id` may be CLI flags or JSON fields.)

Notes:
- Step 3.5 runs **before** Step 3.1 drafting obligations. When an escalation
  opens here, Step 3.1 must not `persist-reply-draft` on the same turn.
- **`question_to_operator` MUST be 简体中文** — it is shown verbatim in the
  Console under「请求操作员答复」for non-technical operators. When copying
  from `escalation_hint.suggested_question`, pass it through unchanged; when
  opening an escalation without a rule match, write plain Chinese (no English
  boilerplate).
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

### Step 4 — Draftable plan (multi-goal)

Endpoint details: `references/shared/bridge-http-api-endpoints.md`.

After Step 3 re-fetch, call the deterministic plan endpoint:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py select-draftable-plan \
  --json '{"goals": <from dispatch_context.goals as name→row map>,
            "facts": <merge reusable_facts.facts + campaign_facts>,
            "signals": <classifier signals>,
            "meta": {}}'
```

Response: `{draftable, escalate, wait, idle, primary_contributor, ...}`.
**`draftable`** lists every active goal with a child skill (including
multiple goals in the same commerce lane, e.g. `product_selection` +
`deliverables_scope`).

For each row in **`escalate`**, open an escalation immediately (human
gate). Those goals are excluded from synthesis this turn.

If **`draftable`** is empty after escalations, write
`approval.unmatched_reply` or `approval.pending_action_*` as before and
skip to Step 6.

**Defer to contract (commerce, default).** When
`campaign_config.defer_terms_to_contract` is true and both
`compensation_negotiation` and `contract_signing` appear in `draftable`,
prefer **`contract_signing` / `kol-contract-coordinator`** as the sole
commerce fragment — skip invoking `kol-compensation-negotiator` (it should
return `{"skipped":"defer_to_contract"}` if invoked anyway).

Goal → child skill (reference):

| Goal | Child skill |
|---|---|
| `interest_qualification` | `kol-interest-qualifier` |
| `product_selection` | `kol-product-selector` |
| `deliverables_scope` | `kol-deliverables-clarifier` |
| `compensation_negotiation` | `kol-compensation-negotiator` |
| `contract_signing` | `kol-contract-coordinator` |
| `logistics` (no address) | `kol-shipping-intake` |
| `logistics` (post-address) | `kol-logistics-tracker` |
| `payout_setup` | `kol-payout-method-intake` |
| `content_production` (no brief) | `kol-brief-sender` |
| `content_review_and_golive` | `kol-content-reviewer` |
| `post_collab_archival` | `kol-archival-writer` |

### Step 5 — Parallel fragment-mode child skills

For **each** row in `draftable`, invoke the bound child skill with
`fragment_mode: true` plus the standard reply inputs:

- `identity_id`, `campaign_id`, `env`, `thread_id`
- `inbound_excerpt`, `thread_history` (verbatim Step 0)
- `flow_hint` per goal (lane, current_goal, missing_facts, …)

**Parallelism:** prefer `delegate_task` with one task per draftable goal
(isolated sub-agents). If delegation is unavailable, run sequentially in
plan order — still collect all fragments before Step 5.5.

Each child returns either:
- `{fragment_mode: true, fragment, proposed_facts, goal, skill}` or
- `{fragment_mode: true, gate: true, reason, goal}` → open escalation for
  that goal; exclude from synthesis
- `{skipped: ...}` or `{error: ...}` → log; exclude from synthesis

Child skills **must not** write facts or open escalations in fragment
mode. See `references/shared/fact-ownership.md`.

### Step 5.5 — Merge proposed facts (single write)

Collect `proposed_facts` from all successful fragments into
`{goal: {fact_key: value}}`. The dispatcher (not the model) validates
disjoint ownership — every key must belong to exactly one goal per
`GOAL_OWNED_FACTS` in the Bridge.

If validation fails, open escalation `fragment_fact_conflict` and skip
drafting.

Otherwise write **once**:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"fragment-merge:<message_id>",
            "namespaces":{"offer":{...},"identity":{...}}}'
```

Group flat keys into namespaces (`offer.*` → `offer`, etc.). Re-fetch
`get-dispatch-context` after the write.

**Proposed vs committed:** deliverables-clarifier fragment mode must use
`offer.deliverable_platforms_proposed` / `offer.deliverable_count_proposed`
(not the committed keys that satisfy `deliverables_scope`). Never propose
`offer.interest_signal` or `offer.agreed_terms` from fragments — classifier
only. See `references/shared/fact-ownership.md`.

### Step 5.6 — Synthesize one reply body

Invoke `kol-reply-synthesizer` with ordered `fragments` from Step 5
(non-gated, non-empty). Receive content-only `{body, thread_id,
conversation_summary}`.

**Body must be new prose only** — do not append prior thread mail (`On …
wrote:`, `>` lines, or pasted `thread_history`). The bridge strips accidental
quotes at persist and adds one Gmail quote block on approve.

Build `contributing` list for persistence:

```json
[
  {"lane": "commerce", "goal": "product_selection", "skill": "kol-product-selector"},
  {"lane": "commerce", "goal": "deliverables_scope", "skill": "kol-deliverables-clarifier"}
]
```

`primary_*` fields = first entry in `contributing` (highest priority).

### Step 5.7 — Persist synthesized draft

Use the toolized persist endpoint (enrichment + event + fact atomically):

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py persist-reply-draft \
  --env <TEST|LIVE> \
  --json '{
    "identity_id": <id>,
    "campaign_id": "<cid>",
    "source_message_id": "<inbound_message_id>",
    "primary_lane": "<first contributing lane>",
    "primary_goal": "<first contributing goal>",
    "child_skill": "kol-reply-synthesizer",
    "child_envelope": {"body": "<synthesized body>", "thread_id": "..."},
    "latest_email": <latest_email from pending_replies>,
    "contributing": [ ... ],
    "conversation_summary": {"bullets": ["...", "..."]}
  }'
```

Pass `conversation_summary` from the synthesizer output verbatim as a
**top-level** persist JSON field (never inside `child_envelope` or
`draft`). Bridge stores it on the approval fact, not in `draft.body`.
Omit only when synthesis returned no valid bullets (rare — prefer
re-running Step 2b).

If synthesis returns `no_fragments_to_synthesize`, open escalation instead.

When a fragment skill returned `campaign_config_incomplete`, open
escalation with `resume_context.missing_config_fields` as before.

Every processed reply must end in exactly one durable outcome:
`kol_reply_draft_ready` (via persist-reply-draft), `open-escalation`, or
`approval.pending_action_*`.

### Step 5.8 — Refinement runs (operator-triggered regeneration)
When the brief is an `approval_refine` (operator clicked 优化/重新生成
on the Approvals page), the input carries `operator_refinement_prompt`
and the full prior `approval.reply_draft` value under
`current_value_json`. In that mode:

**Console path:** the gateway brief uses `write-facts-multi` (not
`persist-reply-draft`) per Console `_REFINE_DRAFT_INSTRUCTIONS`; still
refresh `conversation_summary` via synthesizer (`summary_only` when
needed). **Dispatcher-routed refine** (same `# approval_refine` header
but this skill owns the run): use `persist-reply-draft` below.

- Skip Steps 1–5.6 (no classification, no fragment fan-out).
- If `contributing_skills` lists multiple contributors, re-run fragment
  skills + synthesizer with the refinement prompt appended; otherwise
  re-invoke the single named `child_skill` (legacy single-skill drafts).
- **Conversation summary:** synthesizer runs must refresh
  `conversation_summary`. When only a single child skill re-drafts (no
  synthesizer merge), call `kol-reply-synthesizer` with `summary_only:
  true`, `fragments=[]`, and the same `latest_email` + `thread_history`,
  then pass the returned bullets into persist.
- Do **not** rewrite domain facts on a refinement run — content-only.
- Persist via `persist-reply-draft` only — never `write-facts-multi` on
  `approval.reply_draft` as before. Skip Step 6 label changes.

### Step 6 — Idempotency labels
After Step 5.7 (or escalation-only outcome), apply the Gmail label
`kol-outreach/handled` once per inbound message using the **same operator
inbox** that received the reply:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py mark-reply-handled \
  --env <TEST|LIVE> \
  --message-id "<inbound_message_id>" \
  --identity-id <identity_id> \
  --campaign-id "<campaign_id>" \
  --detected-mailbox-user-id <detected_mailbox_user_id>
```

`--detected-mailbox-user-id` and `--campaign-id` are required whenever
multi-operator Gmail is enabled so labels apply on the correct account.

### Step 7 — Final report
Return a JSON summary covering each processed reply:

```json
[
  {
    "identity_id": 42,
    "campaign_id": "TS8319",
    "env": "TEST",
    "primary_lane": "commerce",
    "primary_goal": "product_selection",
    "primary_skill_invoked": "kol-reply-synthesizer",
    "contributing_skills": [
      {"lane": "commerce", "goal": "product_selection", "skill": "kol-product-selector"},
      {"lane": "commerce", "goal": "deliverables_scope", "skill": "kol-deliverables-clarifier"}
    ],
    "fragments_merged": 2,
    "draft_event_written": true,
    "approval_fact_written": "approval.reply_draft",
    "escalations_opened": []
  }
]
```

## Examples

### Success — multi-goal commerce (product + deliverables)
KOL confirms interest and asks about product + deliverables. Classifier
writes interest facts. Plan returns `draftable`:
`product_selection` + `deliverables_scope`. Fragment skills return two
topic paragraphs + disjoint `proposed_facts`. Dispatcher writes facts
once, synthesizer merges, one `approval.reply_draft` pending.

### Success — single-lane
KOL replies "I'd love to collaborate, what's the budget?". Plan returns
one draftable goal: `deliverables_scope`. Single fragment → synthesizer
still produces one email (trivial merge).

### Success — multi-lane, severity reversal
KOL replies with "package never arrived" + price talk. Plan may return
draftable goals in both fulfillment and commerce; severity signals still
apply — fulfillment fragments may rank first in `contributing` order.
Gated commerce goals go to `escalate` list instead of synthesis.

### Failure — namespace violation
Classifier emits malformed keys. Step 3 hits `FactNamespaceError`. Open
escalation `fact_namespace_violation`, skip drafting.

### Failure — fragment fact conflict
Two fragment skills propose the same fact key. Open
`fragment_fact_conflict` escalation.

### Failure — missing config
`campaign_config` not in the snapshot. Open escalation
`dispatcher_missing_context`. Do NOT proceed.

## Tool failures (HARD — do not improvise)

| Failure | Required action |
|---------|-----------------|
| `select-draftable-plan` / `persist-reply-draft` HTTP **404** | Stop. Escalate `bridge_stale_or_down`. **Do not** `import dispatch_router`, run plugin loaders, or use `execute_code` for routing. |
| `skill_view` file not found | Use only files listed in `available_files` or `bridge-http-api-endpoints.md`. |
| `mark-reply-handled` **503** on handled label | Escalate `gmail_label_error`. Pending-reply missing is OK (Bridge skips it). |
| `anomaly_signals.mailbox_mismatch` | Open escalation `inbound_mailbox_mismatch` before drafting; include both mailbox emails. |
| `FactNamespaceError` on write | Escalate `fact_namespace_violation`; do not munge keys. |
| Fragment `assert_disjoint` conflict | Escalate `fragment_fact_conflict`. |

Allowed tools for deterministic steps: native **`terminal`** with one
`kol_bridge_tool.py` subcommand per call, plus `delegate_task` for fragment child
skills only. **Never** use `execute_code` (including `subprocess` wrappers) or
`curl`/HTTP to call the bridge; never read `kol-ops-bridge` Python source.

## Pitfalls
- The classifier's `active_goals_by_lane` is a **hint**, not the truth.
  Always re-fetch `get-dispatch-context` after writing facts and trust the
  server.
- Side-topics for **wait** goals still via `approval.pending_topics` when
  no fragment was produced — never silently drop a non-draftable action.
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
- Never paraphrase, summarize, or re-order `thread_history` before
  handing it to the classifier or a child skill. The pre-run already
  stripped it to `{from, date, body}` exactly so downstream LLMs see
  the conversation verbatim and avoid re-asking previously-answered
  questions or echoing already-used phrasing.
- When `chase_context.recommended_action == "regenerate"`, **never** end
  the turn with only `approval.pending_action_reply_needed` — the Bridge
  rejects that write. You **must** run Steps 4–5.7 and
  `persist-reply-draft` anchored to `latest_email.message_id`.
- On macOS, **never** invoke bare `python` for bridge CLI — use `python3`
  with the **absolute** path to `kol_bridge_tool.py` (see dispatcher
  `_DISPATCHER_INSTRUCTIONS` / `bridge_agent_contract.cli_invocation_abs`).
  Relative `plugins/...` from `$HOME` yields empty stdout and a stuck run.
  CLI failures emit JSON on **stdout** (`error`/`hint`); empty output + exit 2
  means read stdout — never fall back to `execute_code` (guard blocks it).
- `flow_hint.kol_signaled_next_step` is guidance, not policy. Never
  override what the KOL actually wrote: if they asked a question on
  the current goal, the child skill must answer it even when the hint
  says `true`. The dispatcher's job is to point at the right child
  skill; advancing the goal is the child's call, informed by both the
  hint and the inbound text.
