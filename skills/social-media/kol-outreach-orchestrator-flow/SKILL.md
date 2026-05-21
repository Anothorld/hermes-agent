---
name: kol-outreach-orchestrator-flow
description: Top-level KOL outreach lifecycle map for the v2.4 goal-driven architecture. This skill is documentation/onboarding for the operator; it does NOT itself dispatch turns. Day-to-day routing is handled by `kol-discovery-to-outreach-router` (discovery side) and `kol-reply-dispatcher` (inbound side). Use this skill to understand the 10-goal × 4-lane state machine, the SKILL inventory, and the operator's two approval surfaces (chat shortlist + Gmail drafts + ApprovalsPage / EscalationConsolePage).
trigger: Use when an operator asks "how does the KOL pipeline work end-to-end?", "which skill handles X?", or "where is decision Y made?". Also useful as orientation when onboarding a new campaign brief. Do NOT invoke this skill on the per-turn KOL reply path — that's the dispatcher's job.
tags: ["kol", "orchestrator", "lifecycle", "documentation", "meta"]
---

## Architecture in one diagram

```
                +----------------------+
   campaign --> | kol-campaign-intake  |  (operator free-text → campaign_config)
   brief        +----------+-----------+
                           |
                           v
                +----------------------+
   discovery -> | instagram-kol-       |  (find candidate handles)
                | discovery            |
                +----------+-----------+
                           |
                           v
                +-----------------------------+
                | kol-discovery-to-outreach-  |  (route candidates → cold / reengage)
                | router                      |
                +------+----------------+-----+
                       |                |
                       v                v
        +--------------------+   +-----------------------+
        | kol-cold-outreach  |   | kol-reengagement-     |
        |                    |   | outreach              |
        +---------+----------+   +-----------+-----------+
                  |                          |
                  | first KOL reply lands in inbox
                  v                          v
                +---------------------------------------+
                | kol-email-stage-classifier            |  (side-effect-free
                +---------------------+-----------------+   classification)
                                      |
                                      v
                +---------------------------------------+
                | kol-reply-dispatcher                  |  (1 reads + 1 writes,
                +---+---+---+----+---+---+---+----+-----+   then delegates)
                    |   |   |    |   |   |   |    |
        +-----------+   |   |    |   |   |   |    +----- (publish lane)
        | (commerce lane)|   |    |   (fulfillment lane)         |
        v               v   v    v   v   v   v                  v
+--------------+ +-----+ +-+ +--+ +-+ +-+ +-+               +-----+
|interest-     | |prod-| |dl| |pr| |sh| |lt| |bs|           |c-r| |gb|
|qualifier     | |sel  | |c | |st| |i | |  | |  |           |   | |  |
+--------------+ +-----+ +-+ +-+  +-+ +-+ +-+               +---+ +-+

Legend:
  dlc = kol-deliverables-clarifier        sh = kol-shipping-intake
  pst = kol-pricing-strategist            lt = kol-logistics-tracker
        + kol-compensation-negotiator     bs = kol-brief-sender
  prc = kol-contract-coordinator          c-r = kol-content-reviewer
                                          gb = kol-golive-and-boost

  archival lane: kol-archival-writer (post-engagement)
```

## The 10 goals × 4 lanes

| Lane | Goals (in typical order) |
|---|---|
| commerce | first_contact → interest_qualified → product_locked → deliverables_scope_locked → contract_signed |
| fulfillment | shipped_and_brief_sent (sub: address_collected, tracking_filled, delivered_confirmed, brief_sent) |
| publish | content_review_and_golive (sub: draft_approved, golive_done, boost_handoff_done) |
| meta | engagement_aborted, archival_done |

Each goal has status ∈ `{not_started, active, paused, done, skipped, aborted}`.
The dispatcher reads `active_goals_by_lane` from `get-dispatch-context`
to pick which child SKILL handles the current turn (priority:
commerce > fulfillment > publish > meta, with severity reversal — if a
lower-priority lane has a paused/aborted blocker, it preempts).

## Two approval surfaces (operator's only manual touchpoints)

1. **Chat shortlist approval**: after discovery → routing, the operator
   gets one chat message with the shortlist; one click approves
   cold/reengagement bucketing.
2. **ApprovalsPage**: writes that need human OK before automation
   continues. Backed by `approval.*` facts + a queue:
   - `approval.compensation_paid_above_ceiling`
   - `approval.contract_change_request` (cosmetic clause edits)
   - `approval.identity_drift_review` (new shipping address differs)
   - `approval.shipping_anomaly` (damaged / wrong item)
   - `approval.content_review_escalation`
3. **EscalationConsolePage**: for issues that need an operator to
   actually compose a reply (off-policy product asks, contested
   contract clauses, off-cap price asks, package loss, KOL refusing
   to share address). Different queue from ApprovalsPage; an
   escalation never auto-resolves from an inbound — operator must
   close it explicitly.

## Operator quick reference: "which skill writes which fact?"

| Fact prefix | Owning SKILL(s) |
|---|---|
| `offer.interest_*` | kol-interest-qualifier |
| `offer.product_locked`, `offer.color_variant_locked` | kol-product-selector |
| `offer.deliverables_scope` | kol-deliverables-clarifier |
| `offer.compensation_*` | kol-compensation-negotiator |
| `offer.contract_*` | kol-contract-coordinator |
| `fulfillment.address_*`, `shipping_address` | kol-shipping-intake |
| `fulfillment.tracking_*`, `delivered_*` | kol-logistics-tracker |
| `fulfillment.brief_*` | kol-brief-sender |
| `fulfillment.draft_*`, `revision_*` | kol-content-reviewer |
| `fulfillment.golive_*`, `posted_*`, `boost_*` | kol-golive-and-boost |
| `approval.archival_*` | kol-archival-writer |
| `approval.*` | various (see ApprovalsPage list above) |

## Notification deep-links (DingTalk)

`plugins/kol-ops-bridge/notifier.py` posts markdown messages with
deep-links to `HERMES_KOL_CONSOLE_BASE_URL` for:
- escalations (`/escalations/{id}`)
- approvals (`/approvals/{id}`)
- drafts ready for operator review (`/identities/{id}?campaign=...`)

Webhook + secret read from `HERMES_DINGTALK_WEBHOOK` / `HERMES_DINGTALK_SECRET`.
Failure to notify is logged but never blocks a fact write.

## What this SKILL does NOT do

- ❌ Does not dispatch per-turn replies (dispatcher's job).
- ❌ Does not write any facts (it's documentation).
- ❌ Does not classify inbound (classifier's job).
- ❌ Does not parse campaign briefs (intake's job).

If you find yourself needing to "run the orchestrator", you almost
certainly want `kol-reply-dispatcher` (inbound) or
`kol-discovery-to-outreach-router` (outbound first contact).

## Pitfalls
- Treating this map as the sole source of truth at runtime — the
  goal machine in `plugins/kol-ops-bridge/goals.py` is authoritative.
  This SKILL drifts; the code does not.
- Asking this SKILL to "decide what to do next". It can't. The
  dispatcher reads `get-dispatch-context` and decides per-turn.
- Bypassing the dispatcher with ad-hoc child-skill invocations on
  the inbound path. Always go through the dispatcher so facts and
  goals stay consistent.
