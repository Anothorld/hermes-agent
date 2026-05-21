"""Archive + escalation flow tests."""

from __future__ import annotations


CAMPAIGN_A = "C-archive-A"
CAMPAIGN_B = "C-archive-B"


def _bootstrap_campaign(cal, cid):
    cal.upsert_campaign_config(campaign_id=cid, label="X",
                               sku_whitelist=["SKU-A"],
                               deliverable_platforms=["instagram"],
                               deliverable_count_per_platform=1,
                               paid_ceiling=1000.0,
                               contract_required=True)


def test_archive_collab_writes_relationship_and_facts(cal_db):
    cal = cal_db
    _bootstrap_campaign(cal, CAMPAIGN_A)
    iid = cal.upsert_identity(primary_handle="ivy",
                              default_shipping_address={"city": "NYC", "zip": "10001"})
    cal.archive_collab(
        identity_id=iid, campaign_id=CAMPAIGN_A, outcome="success",
        preferred_skus=["SKU-A"], preferred_mode="gifted",
        avg_revision_rounds=2.0, delivery_quality=0.9,
    )
    rel = cal.get_relationship(iid)
    assert rel["total_collabs"] == 1
    assert rel["last_outcome"] == "success"
    assert rel["preferred_mode"] == "gifted"
    assert rel["last_archived_at"] is not None

    reusable = cal.get_reusable_facts(iid)
    assert reusable["preferred_skus"] == ["SKU-A"]
    assert reusable["default_shipping_address"]["city"] == "NYC"

    facts = cal.latest_facts_for(identity_id=iid, campaign_id=CAMPAIGN_A)
    assert facts["approval.archival_outcome"] == "success"
    assert facts["approval.relationship_synced"] is True


def test_archived_kol_reused_in_second_campaign(cal_db):
    cal = cal_db
    _bootstrap_campaign(cal, CAMPAIGN_A)
    _bootstrap_campaign(cal, CAMPAIGN_B)
    iid = cal.upsert_identity(primary_handle="jack",
                              default_shipping_address={"city": "Tokyo"})
    cal.archive_collab(identity_id=iid, campaign_id=CAMPAIGN_A,
                       outcome="success", preferred_skus=["SKU-A"],
                       preferred_mode="paid")
    # Second campaign — relationship should report repeat_kol.
    cal.upsert_candidate(campaign_id=CAMPAIGN_B, identity_id=iid, source="discovery")
    cal.resolve_candidate_relationships(campaign_id=CAMPAIGN_B)
    cands = cal.list_candidates(CAMPAIGN_B)
    assert cands[0]["relationship_status"] == "repeat_kol"
    reusable = cal.get_reusable_facts(iid)
    assert reusable["default_shipping_address"]["city"] == "Tokyo"
    assert reusable["preferred_skus"] == ["SKU-A"]


def test_escalation_blocks_then_unblocks_goal(cal_db):
    cal = cal_db
    _bootstrap_campaign(cal, CAMPAIGN_A)
    iid = cal.upsert_identity(primary_handle="kira")
    # Seed offer state up to compensation.
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN_A, namespace="offer",
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
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN_A)}
    assert g["compensation_negotiation"]["status"] == "active"

    eid = cal.open_escalation(identity_id=iid, campaign_id=CAMPAIGN_A,
                              goal="compensation_negotiation",
                              reason="paid_over_ceiling",
                              question_to_operator="approve $1500?")
    assert eid is not None
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN_A)}
    assert g["compensation_negotiation"]["status"] == "blocked"
    assert g["compensation_negotiation"]["blocking_escalation_id"] == eid

    cal.resolve_escalation(escalation_id=eid, decision="approve_override",
                           decided_by="op:zoe",
                           operator_facts={"offer.agreed_terms": {"amount": 1500}},
                           final_state="resolved")
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN_A)}
    # After unblock, status returns to active until facts written by skill close it.
    assert g["compensation_negotiation"]["status"] == "active"
    assert g["compensation_negotiation"]["blocking_escalation_id"] is None


def test_escalation_aborted_marks_goal_aborted(cal_db):
    cal = cal_db
    _bootstrap_campaign(cal, CAMPAIGN_A)
    iid = cal.upsert_identity(primary_handle="leo")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN_A, namespace="offer",
                    facts={"offer.outreach_sent": True,
                           "offer.interest_signal": "confirmed"})
    eid = cal.open_escalation(identity_id=iid, campaign_id=CAMPAIGN_A,
                              goal="product_selection",
                              reason="kol_demands_off_whitelist")
    cal.resolve_escalation(escalation_id=eid, decision="reject",
                           decided_by="op:zoe", final_state="aborted")
    g = {x["goal"]: x for x in cal.get_goal_state(identity_id=iid, campaign_id=CAMPAIGN_A)}
    assert g["product_selection"]["status"] == "aborted"


def test_pending_approvals_listed(cal_db):
    cal = cal_db
    _bootstrap_campaign(cal, CAMPAIGN_A)
    iid = cal.upsert_identity(primary_handle="mira")
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN_A, namespace="approval",
                    facts={"approval.over_budget_request": {"amount": 1500}})
    pending = cal.list_pending_approvals()
    assert any(p["fact_key"] == "approval.over_budget_request" for p in pending)

    # Approve → pending list shrinks.
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN_A, namespace="approval",
                    facts={"approval.over_budget_request":
                           {"amount": 1500, "decision": "approved",
                            "decided_by": "op:zoe"}})
    pending2 = cal.list_pending_approvals()
    assert not any(p["fact_key"] == "approval.over_budget_request" for p in pending2)
