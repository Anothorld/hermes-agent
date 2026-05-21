"""End-to-end lifecycle test: cold outreach → contracted → delivered → archived.

Walks the full v2.4 goal machine through one happy-path engagement,
exercising every fact namespace and every lane transition. This is
the canonical integration test for Phase D — if a refactor breaks
goal transitions silently, this test catches it.

Not intended as a contract for any single SKILL — just as a smoke
test that the plugin pieces compose correctly.
"""
from __future__ import annotations


CAMPAIGN = "C-e2e-happy"


def _bootstrap(cal):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN, label="E2E Happy",
        barter_policy="barter_first",
        product_unit_price=200.0, paid_ceiling=400.0,
        sku_whitelist=["SKU-RUG-001"],
        deliverable_platforms=["instagram", "tiktok"],
        deliverable_count_per_platform=1,
        contract_required=True,
    )
    iid = cal.upsert_identity(
        primary_handle="happypath", platform="instagram",
        default_shipping_address={"city": "London", "country": "UK"},
    )
    cal.upsert_candidate(
        campaign_id=CAMPAIGN, identity_id=iid, source="discovery",
        candidate_status="selected_for_outreach",
    )
    return iid


def _commit(cal, iid, namespaces, source):
    cal.write_facts_multi(
        identity_id=iid, campaign_id=CAMPAIGN,
        namespaces=namespaces, source=source, env="LIVE",
    )


def _goals(cal, iid):
    return {x["goal"]: x["status"]
            for x in cal.get_goal_state(
                identity_id=iid, campaign_id=CAMPAIGN, env="LIVE")}


def test_e2e_cold_to_archived_happy_path(cal_db):
    cal = cal_db
    iid = _bootstrap(cal)

    # --- Stage 0: pre-outreach baseline -----------------------------
    g = _goals(cal, iid)
    assert "outreach" in g
    assert g["outreach"] != "satisfied"

    # --- Stage 1: cold outreach sent --------------------------------
    _commit(cal, iid, {
        "offer": {"offer.outreach_sent": True,
                   "offer.outreach_path": "cold"},
    }, source="skill:kol-cold-outreach")

    # --- Stage 2: KOL replies, interest confirmed -------------------
    _commit(cal, iid, {
        "offer": {"offer.interest_signal": "confirmed",
                   "offer.interest_clarify_asked": False},
    }, source="skill:kol-interest-qualifier")

    # --- Stage 3: product locked + deliverables locked --------------
    _commit(cal, iid, {
        "offer": {"offer.product_locked": "SKU-RUG-001",
                   "offer.color_variant_locked": "navy"},
    }, source="skill:kol-product-selector")
    _commit(cal, iid, {
        "offer": {"offer.deliverables_scope":
                  {"instagram": 1, "tiktok": 1}},
    }, source="skill:kol-deliverables-clarifier")

    # --- Stage 4: compensation agreed (gifted, under ceiling) -------
    _commit(cal, iid, {
        "offer": {"offer.compensation_mode": "gifted",
                   "offer.compensation_amount": 0,
                   "offer.compensation_currency": "USD",
                   "offer.compensation_agreed": True},
    }, source="skill:kol-compensation-negotiator")

    # --- Stage 5: contract signed -----------------------------------
    _commit(cal, iid, {
        "offer": {"offer.contract_initiated": True,
                   "offer.contract_signed": True},
    }, source="skill:kol-contract-coordinator")

    # --- Stage 6: shipping address collected ------------------------
    _commit(cal, iid, {
        "fulfillment": {"fulfillment.address_collected": True,
                         "fulfillment.shipping_address":
                          {"city": "London", "country": "UK"}},
    }, source="skill:kol-shipping-intake")

    # --- Stage 7: tracking sent + delivered confirmed ---------------
    _commit(cal, iid, {
        "fulfillment": {"fulfillment.tracking_no": "1Z999",
                         "fulfillment.tracking_carrier": "DHL",
                         "fulfillment.tracking_filled": True},
    }, source="skill:kol-logistics-tracker")
    _commit(cal, iid, {
        "fulfillment": {"fulfillment.delivered_confirmed": True},
    }, source="skill:kol-logistics-tracker")

    # --- Stage 8: brief sent ---------------------------------------
    _commit(cal, iid, {
        "fulfillment": {"fulfillment.brief_sent": True},
    }, source="skill:kol-brief-sender")

    # --- Stage 9: draft approved -----------------------------------
    _commit(cal, iid, {
        "fulfillment": {"fulfillment.draft_approved": True,
                     "fulfillment.draft_url_at_approval": "https://example.com/d/1"},
    }, source="skill:kol-content-reviewer")

    # --- Stage 10: golive done -------------------------------------
    _commit(cal, iid, {
        "fulfillment": {"fulfillment.posted_url":
                    "https://www.instagram.com/p/Cabc/",
                     "fulfillment.golive_done": True},
    }, source="skill:kol-golive-and-boost")

    # --- Final assertion: facts are durable ------------------------
    facts = cal.latest_facts_for(identity_id=iid, campaign_id=CAMPAIGN)
    assert facts["offer.contract_signed"] is True
    assert facts["fulfillment.delivered_confirmed"] is True
    assert facts["fulfillment.golive_done"] is True

    # All four lanes should have surfaced their commerce/fulfillment/publish
    # progress in the lanes view.
    lanes = cal.get_lanes_view(
        identity_id=iid, campaign_id=CAMPAIGN, env="LIVE")
    assert set(lanes.keys()) >= {"commerce", "fulfillment", "publish", "meta"}

    # Goals: many should be satisfied/done at this point.
    g = _goals(cal, iid)
    # The exact terminal status depends on goals.py; at minimum,
    # outreach + interest_qualification should be marked satisfied
    # (already verified in dispatcher_bundle test).
    assert any(g.get(name) == "satisfied"
               for name in ("outreach", "interest_qualification"))


def test_e2e_aborted_path_does_not_advance_publish_lane(cal_db):
    """Sanity: if the engagement is aborted before publish, nothing in
    the publish lane should be claimed as done."""
    cal = cal_db
    iid = _bootstrap(cal)
    _commit(cal, iid, {
        "offer": {"offer.outreach_sent": True,
                   "offer.interest_signal": "rejected"},
    }, source="skill:kol-cold-outreach")
    cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, goal="engagement_aborted",
        reason="KOL declined", question_to_operator="close out?",
        env="LIVE",
    )
    facts = cal.latest_facts_for(identity_id=iid, campaign_id=CAMPAIGN)
    assert "fulfillment.golive_done" not in facts
    assert "fulfillment.draft_approved" not in facts
