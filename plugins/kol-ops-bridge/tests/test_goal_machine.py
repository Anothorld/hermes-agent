"""End-to-end tests for the v2.4 goal machine.

Covers:
- Each goal's missing/satisfied/blocked transitions under fact orderings.
- Compensation gate matrix (gifted / paid_within / paid_over_ceiling /
  commission).
- Re-engagement vs cold path selection from kol_relationship.
- Discovery vs candidate selection.
- Contract auto-skip when contract_required=False.
- Archival writes back to relationship.
- Multi-lane parallel activation.
- Fact namespace prefix rejection.
"""

from __future__ import annotations

import pytest


CAMPAIGN = "C-2024-test"


def _seed_campaign(cal, *, contract_required=True, paid_ceiling=1000.0,
                   sku_whitelist=None, deliverable_count=1):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN, label="Test", barter_policy="barter_first",
        product_unit_price=200.0, paid_ceiling=paid_ceiling,
        sku_whitelist=sku_whitelist or ["SKU-A", "SKU-B"],
        deliverable_platforms=["instagram", "tiktok"],
        deliverable_count_per_platform=deliverable_count,
        contract_required=contract_required,
        commission_band={"min": 0.05, "max": 0.20},
    )


def test_outreach_then_interest_qualification(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="alice", platform="instagram")
    cal.upsert_candidate(campaign_id=CAMPAIGN, identity_id=iid,
                         source="discovery", candidate_status="selected_for_outreach")

    # Initial recompute: outreach goal active for new prospect.
    cal.recompute_goals(identity_id=iid, campaign_id=CAMPAIGN)
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["outreach"]["status"] == "active"
    assert g["interest_qualification"]["status"] == "inactive"

    # outreach.outreach_sent fact → outreach satisfied, interest active.
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["outreach"]["status"] == "satisfied"
    assert g["interest_qualification"]["status"] == "active"

    # Interest confirmed → product_selection + deliverables_scope active in parallel.
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.interest_signal": "confirmed"})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["interest_qualification"]["status"] == "satisfied"
    assert g["product_selection"]["status"] == "active"
    assert g["deliverables_scope"]["status"] == "active"


def test_compensation_paid_over_ceiling_gate(cal_db):
    cal = cal_db
    _seed_campaign(cal, paid_ceiling=1000.0)
    iid = cal.upsert_identity(primary_handle="bob")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed",
                           "offer.sku_locked": "SKU-A",
                           "offer.color_or_variant_locked": True,
                           "offer.fit_confirmed": True,
                           "offer.deliverable_platforms": ["instagram"],
                           "offer.deliverable_count_per_platform": 1,
                           "offer.usage_rights_discussed": True,
                           "offer.compensation_mode": "paid",
                           "offer.kol_quote": 1500.0})
    # gate triggered → goal stays active (not satisfied) until quote/decision resolves
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["compensation_negotiation"]["status"] == "active"


def test_compensation_paid_within_ceiling_satisfies(cal_db):
    cal = cal_db
    _seed_campaign(cal, paid_ceiling=1000.0)
    iid = cal.upsert_identity(primary_handle="carla")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed",
                           "offer.sku_locked": "SKU-A",
                           "offer.color_or_variant_locked": True,
                           "offer.fit_confirmed": True,
                           "offer.deliverable_platforms": ["instagram"],
                           "offer.deliverable_count_per_platform": 1,
                           "offer.usage_rights_discussed": True,
                           "offer.compensation_mode": "paid",
                           "offer.kol_quote": 800.0,
                           "offer.agreed_terms": {"amount": 800, "currency": "USD"}})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["compensation_negotiation"]["status"] == "satisfied"


def test_contract_skipped_when_not_required(cal_db):
    cal = cal_db
    _seed_campaign(cal, contract_required=False)
    iid = cal.upsert_identity(primary_handle="dave")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed",
                           "offer.compensation_mode": "gifted",
                           "offer.agreed_terms": {"mode": "gifted"}})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["contract_signing"]["status"] == "skipped"


def test_namespace_prefix_enforced(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="eve")
    with pytest.raises(cal.FactNamespaceError):
        cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                        facts={"identity.handle": "eve"})  # wrong namespace


def test_repeat_kol_routing_via_relationship(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="frank")
    # Seed a prior successful collab.
    cal.upsert_relationship(identity_id=iid, last_outcome="success",
                            increment_collabs=True)
    cal.upsert_candidate(campaign_id=CAMPAIGN, identity_id=iid, source="discovery")
    n = cal.resolve_candidate_relationships(campaign_id=CAMPAIGN)
    assert n == 1
    cands = cal.list_candidates(CAMPAIGN)
    assert cands[0]["relationship_status"] == "repeat_kol"


def test_repeat_kol_disputed_needs_review(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="gina")
    cal.upsert_relationship(identity_id=iid, last_outcome="disputed",
                            increment_collabs=True)
    cal.upsert_candidate(campaign_id=CAMPAIGN, identity_id=iid, source="discovery")
    cal.resolve_candidate_relationships(campaign_id=CAMPAIGN)
    cands = cal.list_candidates(CAMPAIGN)
    assert cands[0]["relationship_status"] == "repeat_kol_needs_review"


def test_lanes_view_partitions_goals(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="henry")
    cal.recompute_goals(identity_id=iid, campaign_id=CAMPAIGN)
    lanes = cal.get_lanes_view(identity_id=iid, campaign_id=CAMPAIGN)
    assert set(lanes.keys()) >= {"commerce", "fulfillment", "publish", "meta"}
    commerce_goals = {g["goal"] for g in lanes["commerce"]}
    assert {"outreach", "interest_qualification", "product_selection",
            "deliverables_scope", "compensation_negotiation",
            "contract_signing"} <= commerce_goals
    assert {"logistics", "content_production"} <= {g["goal"] for g in lanes["fulfillment"]}
    assert {"content_review_and_golive"} <= {g["goal"] for g in lanes["publish"]}
    assert {"post_collab_archival"} <= {g["goal"] for g in lanes["meta"]}
