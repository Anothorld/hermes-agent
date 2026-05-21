---
name: kol-archival-writer
description: After a campaign engagement closes (status `done` or `aborted`), back-fills durable cross-campaign facts on the KOL's `kol_identity` and `kol_relationship` rows: total_collabs, last_outcome, default_shipping_address (if drift was approved), preferred_compensation_mode, response_time_avg_h, last_engaged_at, plus a one-line `relationship_notes` summary. Writes nothing to per-campaign `kol_facts` (that namespace is closed); writes only to identity/relationship via dedicated bridge endpoints. Idempotent on `approval.archival_done==true`.
trigger: Invoked by `kol-reply-dispatcher` (or a closing cron) when `goals.archival.status == "active"` AND `approval.archival_done != true`. Pre-condition: the campaign-level closing goal (`content_review_and_golive` done OR `engagement_aborted` done) must already be marked.
tags: ["kol", "archival", "kol_identity", "kol_relationship", "meta-lane"]
---

## Goal
Promote learnings from this campaign into the KOL's persistent
profile so the NEXT campaign starts with a smarter cold-vs-reengage
decision and pre-populated defaults.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Identity writes are gated.** `default_shipping_address` is only
  promoted when `approval.identity_drift_review.decision == "approved"`
  for the address change. Otherwise leave identity address untouched.
- **No fact-namespace writes.** Per-campaign `kol_facts` is frozen
  after archival; this skill writes only via identity/relationship
  endpoints.
- **Idempotent.** If `approval.archival_done==true`, abort
  `{"skipped":"already_archived"}`.
- **Closing goal precondition.** Aborts if neither
  `content_review_and_golive` nor `engagement_aborted` is `done`.

## Inputs
1. `identity_id`, `campaign_id`, `env`.
2. Optional `operator_summary` (free-text one-line note appended to
   `relationship_notes`).

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `goals.archival.status`, `goals.content_review_and_golive.status`,
  `goals.engagement_aborted.status`.
- `relationship.total_collabs` (current).
- All `offer.*` keys (latest), `fulfillment.*` keys, `fulfillment.*` keys,
  `approval.identity_drift_review` if present.
- Inbox metadata (response_time_avg_h is computed by the bridge or
  falls back to the existing relationship value).

### Step 2 — Derive update payload

**Outcome derivation** (one of, picked in order):
1. `engagement_aborted.status == "done"` → `last_outcome = "aborted"`
   + capture `last_outcome_reason` from
   `approval.engagement_abort_reason` if present.
2. `fulfillment.golive_done == true` → `last_outcome = "delivered"`.
3. `fulfillment.draft_approved && !fulfillment.golive_done` →
   `last_outcome = "approved_no_golive"` (rare; treat like delivered).
4. Otherwise → `last_outcome = "incomplete"` (defensive).

**Counter increments:**
- `total_collabs += 1` when `last_outcome` ∈ `{delivered, approved_no_golive}`.
- `total_aborted += 1` when `last_outcome == "aborted"`.

**Preferred compensation mode** (relationship-level):
- If `last_outcome == "delivered"` AND `offer.compensation_mode` is set,
  copy it to `relationship.preferred_compensation_mode`.

**Default shipping address (identity-level):**
- ONLY if `approval.identity_drift_review.decision == "approved"`
  AND `fulfillment.shipping_address` was used this campaign:
  set `kol_identity.default_shipping_address` to that snapshot.
- Else: do not touch identity address.

**Relationship notes:**
- Compose a one-line summary like:
  `"<campaign_id>: <last_outcome>; mode=<mode>; sku=<sku>; revisions=<n>"`
- Append `operator_summary` if provided (separated by ` — `).
- Push to `relationship.relationship_notes` (append, do not replace).

**Last engaged at:**
- Set `relationship.last_engaged_at = now`.

### Step 3 — Write via the bridge CLI
All archival writes go through the deterministic CLI — **never** craft
raw HTTP or SQL. Two atomic calls:

1. **Archive the identity / relationship** (single transaction —
   identity row + relationship row + closing facts):
   ```
   python <PROJECT>/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
     archive-identity --identity-id <id> --campaign-id <cid> \
     --outcome <delivered|cancelled|no_show|...> \
     --decided-by skill:archival-writer \
     --json @/tmp/archive.json
   ```
   The JSON body carries the optional `identity_updates`,
   `relationship_updates`, and `post_mortem_facts` blocks (see
   `ArchiveBody` schema). If the bridge rejects with
   `endpoint_not_available`, abort with
   `{"error":"archival_endpoints_not_available"}` and log a TODO
   rather than freelancing fact writes.

2. **Stamp the per-campaign closure flag** (must be the LAST write):
   ```
   python <PROJECT>/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
     write-facts-multi --env TEST|LIVE --identity-id <id> --json @/tmp/closing.json
   ```
   The body sets `approval.archival_done=true` +
   `approval.archival_done_at` under the `approval` namespace.

### Step 4 — Return result envelope
```json
{
  "skill": "kol-archival-writer",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "subject": null,
  "body": null,
  "branch_action": "archived",
  "last_outcome": "delivered",
  "identity_updated": {"default_shipping_address": false},
  "relationship_updated": {"total_collabs": 4, "preferred_compensation_mode": "paid",
                            "relationship_notes_appended": true,
                            "last_engaged_at": "<iso8601>"},
  "facts_written": {"meta": 2}
}
```

This skill never drafts an email. `body: null` always.

## Examples

### Delivered + drift approved
- `golive_done=true`, `approval.identity_drift_review.decision="approved"`.
- Updates: `total_collabs=4 (was 3)`, `preferred_compensation_mode="paid"`,
  `default_shipping_address=<new Berlin>`, notes appended, archival_done=true.

### Aborted
- `engagement_aborted.status="done"`, reason="contract_rejected".
- Updates: `total_aborted=1 (was 0)`, `last_outcome="aborted"`,
  `last_outcome_reason="contract_rejected"`, no compensation/address
  promotion, notes appended.

### Idempotent
`approval.archival_done=true` already. Aborts `{"skipped":"already_archived"}`.

## Pitfalls
- Promoting a new shipping address WITHOUT operator approval — silently
  reshapes future campaigns. Always gate on
  `approval.identity_drift_review.decision == "approved"`.
- Overwriting `relationship_notes` instead of appending. The notes
  field is a running ledger, not a current-state field.
- Forgetting to increment `total_collabs` (or wrongly counting an
  aborted engagement toward it). Branch on outcome.
- Writing more `kol_facts.*` keys after `approval.archival_done=true`.
  The fact namespace is closed.
