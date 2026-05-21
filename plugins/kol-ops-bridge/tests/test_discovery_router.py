"""Tests for the deterministic Discovery → Outreach router."""

from __future__ import annotations

import importlib
import sys


CAMPAIGN = "C-router-test"


def _router(cal):
    # Pull the same loader the conftest set up.
    return sys.modules["kol_ops_bridge_pkg.discovery_router"]


def _seed_campaign(cal):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN, label="Router test",
        barter_policy="barter_first", product_unit_price=200.0,
        paid_ceiling=1000.0, sku_whitelist=["SKU-A"],
        deliverable_platforms=["instagram"], deliverable_count_per_platform=1,
        contract_required=False,
    )


def _add_candidate(cal, *, handle, last_outcome=None, total_collabs=0):
    iid = cal.upsert_identity(primary_handle=handle, platform="instagram")
    if total_collabs > 0 or last_outcome is not None:
        cal.upsert_relationship(
            identity_id=iid,
            increment_collabs=(total_collabs > 0),
            last_outcome=last_outcome,
        )
        # Force total_collabs to the requested count for >1 cases.
        for _ in range(max(0, total_collabs - 1)):
            cal.upsert_relationship(identity_id=iid, increment_collabs=True)
    cal.upsert_candidate(
        campaign_id=CAMPAIGN, identity_id=iid, source="discovery",
        candidate_status="discovered",
    )
    return iid


def test_route_discovery_partitions_pool(cal_db):
    cal = cal_db
    router = _router(cal)
    _seed_campaign(cal)

    new1 = _add_candidate(cal, handle="alice")
    new2 = _add_candidate(cal, handle="bob")
    repeat_ok = _add_candidate(cal, handle="carol",
                               last_outcome="satisfied", total_collabs=2)
    repeat_risky = _add_candidate(cal, handle="dave",
                                  last_outcome="disputed", total_collabs=1)

    out = router.route_discovery_pool(
        campaign_id=CAMPAIGN, env="LIVE", selected_by="agent",
    )

    assert sorted(out["routed_to_cold"]) == sorted([new1, new2])
    assert out["routed_to_reengagement"] == [repeat_ok]
    assert len(out["needs_review_escalations"]) == 1
    assert out["rejected"] == []

    # candidate_status flipped only for selected ones.
    rows = {c["identity_id"]: c for c in cal.list_candidates(CAMPAIGN)}
    assert rows[new1]["candidate_status"] == "selected_for_outreach"
    assert rows[new2]["candidate_status"] == "selected_for_outreach"
    assert rows[repeat_ok]["candidate_status"] == "selected_for_outreach"
    assert rows[repeat_risky]["candidate_status"] != "selected_for_outreach"

    # outreach_path facts written.
    facts_alice = cal.latest_facts_for(identity_id=new1, campaign_id=CAMPAIGN)
    assert facts_alice.get("identity.outreach_path") == "cold"
    facts_carol = cal.latest_facts_for(identity_id=repeat_ok, campaign_id=CAMPAIGN)
    assert facts_carol.get("identity.outreach_path") == "reengagement"


def test_route_discovery_is_idempotent(cal_db):
    cal = cal_db
    router = _router(cal)
    _seed_campaign(cal)
    _add_candidate(cal, handle="alice")

    first = router.route_discovery_pool(campaign_id=CAMPAIGN, env="LIVE")
    second = router.route_discovery_pool(campaign_id=CAMPAIGN, env="LIVE")

    assert len(first["routed_to_cold"]) == 1
    assert second["routed_to_cold"] == []
    assert len(second["skipped_already_routed"]) == 1


def test_route_discovery_rejects_unknown_env(cal_db):
    cal = cal_db
    router = _router(cal)
    _seed_campaign(cal)
    try:
        router.route_discovery_pool(campaign_id=CAMPAIGN, env="STAGING")
    except ValueError as exc:
        assert "env" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for invalid env")
