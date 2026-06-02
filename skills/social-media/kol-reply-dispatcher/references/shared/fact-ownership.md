# Fact ownership (fragment-mode dispatch)

Each commerce-lane child skill in **fragment mode** may only propose fact
keys listed for its goal. The dispatcher merges proposals via
`assert_disjoint` (Bridge: `dispatch_router.GOAL_OWNED_FACTS`) before a
single `write-facts-multi` call.

Source of truth in code:
`hermes-agent/plugins/kol-ops-bridge/dispatch_router.py` → `GOAL_OWNED_FACTS`.

## commerce lane

| Goal | Owned fact keys |
|------|-----------------|
| `interest_qualification` | `offer.interest_clarify_asked`, `offer.interest_clarify_question`, `identity.contact_role`, `identity.manager_name`, `identity.manager_email` (not `offer.interest_signal` — classifier only) |
| `product_selection` | `offer.sku_locked`, `offer.color_or_variant_locked`, `offer.fit_confirmed`, `offer.sku_requested`, `offer.proposed_skus` |
| `deliverables_scope` | `offer.deliverable_platforms_proposed`, `offer.deliverable_count_proposed`, `offer.usage_rights_discussed`, `offer.deliverable_count_per_platform_requested` |

**Product selection:** use `offer.proposed_skus` when proposing options;
use `offer.sku_locked` / `offer.color_or_variant_locked` / `offer.fit_confirmed`
only when the KOL has confirmed a single variant in this turn (Branch A).

**Interest / compensation:** never propose `offer.interest_signal` or
`offer.agreed_terms` from fragment mode — those flip goal_state to
`satisfied` and belong to the classifier on a later inbound.

**Proposed vs committed:** `deliverables_scope` goal satisfaction requires
`offer.deliverable_platforms` and `offer.deliverable_count_per_platform`
(committed). Fragment-mode deliverables-clarifier must use `*_proposed`
keys only; the classifier promotes to committed on a later inbound when
the KOL agrees. Writing committed keys from a fragment would incorrectly
satisfy the goal in the same turn.
| `compensation_negotiation` | `offer.compensation_mode`, `offer.proposed_amount`, `offer.proposed_basis`, `offer.proposed_currency`, `offer.kol_paid_quote`, `offer.barter_attempted`, `offer.rate_requested`, `offer.paid_hold_sent` (not `offer.agreed_terms` — classifier only on KOL accept; `paid_hold_sent` is legacy-compatible with `rate_requested`) |
| `contract_signing` | `offer.contract_sent`, `offer.contract_signed` |

## fulfillment lane

| Goal | Owned fact keys |
|------|-----------------|
| `logistics` | `fulfillment.address_collected`, `fulfillment.shipping_method`, `fulfillment.tracking_filled`, `fulfillment.delivered_confirmed` |
| `payout_setup` | `payout.method_collected` |
| `content_production` | `offer.brief_sent`, `offer.draft_submitted` |

## publish / meta

| Goal | Owned fact keys |
|------|-----------------|
| `content_review_and_golive` | `offer.review_verdict`, `offer.posted_url`, `offer.boost_assets_status` |
| `post_collab_archival` | `approval.archival_outcome`, `approval.relationship_synced`, `approval.preferred_skus_synced`, `approval.preferred_mode_synced`, `approval.followups_pending` |

## Classifier layer (Step 3, non-fragment)

Inbound classifier writes use `source=email:<message_id>` and may set
committed keys (`offer.interest_signal`, `offer.deliverable_platforms`, …).
The Bridge runs `classifier_facts.sanitize_classifier_namespaces` when
`signals` are passed on the same `write-facts-multi` call — inquiry signals
(`asks_deliverables`, `asks_budget`, `interest_unclear`, …) trigger rewrites
to `*_proposed` or downgrades (`confirmed` → `needs_more_info`). Fragment
ownership rules do **not** apply to the classifier; this layer is separate.

Preview: `kol_bridge_tool.py sanitize-classifier-facts --json '{namespaces,signals}'`.

## Rules

1. Fragment-mode child skills **must not** call `write-facts-multi` — only
   return `proposed_facts` in their JSON envelope.
2. Overlapping keys across goals in one turn → dispatcher opens
   `fragment_fact_conflict` escalation; no draft.
3. Keys outside a goal's ownership set → same escalation path.
4. Human-gate topics (`gate: true` from a fragment skill) are **excluded**
   from synthesis; dispatcher opens escalation for that goal instead.
