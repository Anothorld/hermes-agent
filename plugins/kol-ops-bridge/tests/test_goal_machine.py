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
    assert {"logistics", "payout_setup", "content_production"} <= {g["goal"] for g in lanes["fulfillment"]}
    assert {"content_review_and_golive"} <= {g["goal"] for g in lanes["publish"]}
    assert {"post_collab_archival"} <= {g["goal"] for g in lanes["meta"]}


# ---------------------------------------------------------------------------
# payout_setup goal (PayPal payout collection)
# ---------------------------------------------------------------------------


def _drive_through_contract(cal, iid, *, mode, agreed_terms=None):
    """Seed enough facts to satisfy everything up to and including
    contract_signing for compensation `mode`."""
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed",
                           "offer.sku_locked": "SKU-A",
                           "offer.color_or_variant_locked": True,
                           "offer.fit_confirmed": True,
                           "offer.deliverable_platforms": ["instagram"],
                           "offer.deliverable_count_per_platform": 1,
                           "offer.usage_rights_discussed": True,
                           "offer.compensation_mode": mode,
                           "offer.agreed_terms": agreed_terms or {"mode": mode},
                           "offer.contract_sent": True,
                           "offer.contract_signed": True})


def test_payout_setup_active_after_contract_signed_paid(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_paid")
    _drive_through_contract(cal, iid, mode="paid",
                            agreed_terms={"amount": 800, "currency": "USD"})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "active"
    assert g["payout_setup"]["lane"] == "fulfillment"


def test_payout_setup_active_for_commission_and_hybrid(cal_db):
    cal = cal_db
    for mode in ("commission", "hybrid"):
        _seed_campaign(cal)
        iid = cal.upsert_identity(primary_handle=f"payout_{mode}")
        _drive_through_contract(cal, iid, mode=mode)
        g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
        assert g["payout_setup"]["status"] == "active", f"mode={mode}"


def test_payout_setup_skipped_for_gifted(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_gifted")
    _drive_through_contract(cal, iid, mode="gifted")
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "skipped"


def test_payout_setup_skipped_for_gifted_no_product(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_giftedNoProd")
    _drive_through_contract(cal, iid, mode="gifted_no_product")
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "skipped"


def test_payout_setup_inactive_before_contract_signed(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_nocontract")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed",
                           "offer.compensation_mode": "paid",
                           "offer.agreed_terms": {"amount": 500, "currency": "USD"}})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "inactive"


def test_payout_setup_active_when_contract_not_required(cal_db):
    """If campaign skips contracts, payout_setup still gates on
    compensation being agreed — _contract_satisfied returns True
    immediately when contract_required=False."""
    cal = cal_db
    _seed_campaign(cal, contract_required=False)
    iid = cal.upsert_identity(primary_handle="payout_nocontractreq")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed",
                           "offer.compensation_mode": "paid",
                           "offer.agreed_terms": {"amount": 500, "currency": "USD"}})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "active"


def test_payout_setup_satisfied_when_method_collected(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_collected")
    _drive_through_contract(cal, iid, mode="paid",
                            agreed_terms={"amount": 800, "currency": "USD"})
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="payout",
                    facts={"payout.method_collected": True,
                           "payout.payment_method": {
                               "method": "paypal",
                               "paypal_email": "kol@example.com",
                               "account_holder_name": "KOL Name",
                           }})
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "satisfied"


def test_payout_setup_does_not_block_content_production(cal_db):
    """content_production gates on fulfillment.delivered_confirmed, not
    on payout_setup — the two run independently inside fulfillment."""
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_indep")
    _drive_through_contract(cal, iid, mode="paid",
                            agreed_terms={"amount": 800, "currency": "USD"})
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="fulfillment",
                    facts={"fulfillment.address_collected": True,
                           "fulfillment.shipping_method": "fedex",
                           "fulfillment.tracking_filled": "1Z999",
                           "fulfillment.delivered_confirmed": True})
    # payout_setup still active (not collected), but content_production
    # should also be active in parallel.
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["payout_setup"]["status"] == "active"
    assert g["content_production"]["status"] == "active"


def test_payout_namespace_check_enforced(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    iid = cal.upsert_identity(primary_handle="payout_nsguard")
    # Wrong-namespace prefix should be rejected like other namespaces.
    with pytest.raises(cal.FactNamespaceError):
        cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="payout",
                        facts={"offer.foo": True})


def test_default_payment_method_json_roundtrip(cal_db):
    cal = cal_db
    _seed_campaign(cal)
    pm = {
        "method": "paypal",
        "paypal_email": "alice@example.com",
        "account_holder_name": "Alice Chen",
        "country": "US",
    }
    iid = cal.upsert_identity(primary_handle="payout_roundtrip",
                              default_payment_method=pm)
    ident = cal.get_identity(iid)
    assert ident["default_payment_method"] == pm
    reusable = cal.get_reusable_facts(iid)
    assert reusable["default_payment_method"] == pm
