---
name: kol-discovery-to-outreach-router
description: After a campaign's KOL discovery pool is populated, this skill calls the Bridge's deterministic Discovery to Outreach router to partition the pool into new_prospect / repeat_kol / repeat_kol_needs_review / rejected, mark cold vs re-engagement outreach, and open escalations for historically risky KOLs. Drafts NO email; never sends; one tool call.
trigger: When the user says any of "process the discovery pool", "route discovered KOLs to outreach", "split candidates into cold vs reengagement", "after discovery, decide who gets which outreach", or when the post-approval orchestrator needs to confirm outreach assignment before invoking first-outreach draft skills.
tags: ["kol", "discovery", "router", "outreach", "relationship", "repeat-kol"]
---

## Goal
Partition a populated `campaign_candidates` pool into outreach paths
deterministically. The skill is a thin shell over a single Bridge tool —
the LLM does **no** classification, **no** SQL, and writes **no** prose.

The new-vs-repeat decision and escalation criteria live entirely on the
Bridge (`plugins/kol-ops-bridge/discovery_router.py`). Downstream
outreach skills must trust the routing facts written by the Bridge and
never re-derive them.

## Runtime Contract
- Profile: `outreach-operator` (RBAC: operator+).
- Bridge is the only CAL writer/reader. Forbidden: `cal.py` import,
  direct `~/.hermes/kol-ops-bridge/cal.db` access, ad-hoc SQL,
  `execute_code` against the DB.
- `env` is mandatory (`TEST` or `LIVE`).
- Drafts no email and never sends mail. The post-approval orchestrator
  invokes `kol-cold-outreach` / `kol-reengagement-outreach` after this
  routing step when the operator has approved candidates.
- Idempotent: re-running on an already-routed pool only acts on
  candidates whose `candidate_status='discovered'`.

## Inputs
1. `campaign_id` (mandatory).
2. `env` (`TEST` or `LIVE`, mandatory).
3. (Optional) `operator_note` — one-line summary attached to any opened
   escalations.

## Procedure

### Step 1 — Run the deterministic router
One call. The Bridge resolves relationships, partitions the pool,
selects candidates for outreach, writes `identity.outreach_path` facts,
and opens `repeat_kol_needs_review` escalations atomically.

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py route-discovery \
  --campaign-id "<campaign_id>" --env <TEST|LIVE> \
  --selected-by agent \
  [--operator-note "<one-line risk summary used for any opened escalations>"]
```

Routing rules (enforced server-side — do **not** second-guess):

| relationship_status        | action                                           |
| -------------------------- | ------------------------------------------------ |
| `new_prospect`             | select for outreach + `identity.outreach_path=cold` |
| `repeat_kol`               | select for outreach + `identity.outreach_path=reengagement` |
| `repeat_kol_needs_review`  | open `reengagement_outreach` escalation          |
| `rejected`                 | leave alone                                      |

Risky `last_outcome` values that force `repeat_kol_needs_review` are
classified by the Bridge from the `kol_relationship` row; the LLM has
no veto.

### Step 2 — Hand off
The CLI prints the canonical summary on stdout. Forward it as the
final assistant message verbatim:

```json
{
  "campaign_id": "...",
  "env": "TEST",
  "routed_to_cold": [<id>, ...],
  "routed_to_reengagement": [<id>, ...],
  "needs_review_escalations": [<escalation_id>, ...],
  "rejected": [<id>, ...],
  "skipped_already_routed": [<id>, ...]
}
```

Then explicitly **stop**. The router does not invoke draft skills. After
operator approval, the post-approval orchestrator must invoke
`kol-cold-outreach` / `kol-reengagement-outreach` for the approved IDs
and persist returned drafts as approval records.

## Examples

### Success
12 candidates discovered (9 new, 2 repeat OK, 1 prior `disputed`):

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py route-discovery \
  --campaign-id 2026Q3-summer --env LIVE
```
→ `routed_to_cold=[…9…]`, `routed_to_reengagement=[…2…]`,
`needs_review_escalations=[123]`, `rejected=[]`. Web Kanban shows two
columns lit up in the Outreach lane plus an entry in the Escalation
Console.

### Failure modes (all graded)
- LLM drafts any email → BUG. The router never composes prose.
- LLM second-guesses `relationship_status` from chat heuristic
  ("looks fine to me") → forbidden; only operator approval via Web /
  escalation resolution can flip it.
- LLM splits the work back into `select-candidates` + `write-facts` +
  `open-escalation` chains → forbidden; that path is preserved only as
  internal Bridge implementation, not for skills.
- Calls `cal.py` / direct SQL / `execute_code` → forbidden by Bridge
  contract.

## Pitfalls
- The CLI always returns JSON on stdout; errors are JSON too
  (`{"error": "...", "status": 400}`). Never swallow them.
- A candidate can move new→needs_review across runs (e.g. an archive
  event flips `last_outcome` to disputed mid-flight). Re-running the
  skill is safe and will only act on candidates still in the
  `discovered` state.
- `operator_note` is attached only to escalations opened in this
  invocation — it is a per-run summary, not a per-candidate note.
