"""Tests for ``cal.merge_campaigns`` (one-product-one-campaign migration)."""

from __future__ import annotations

import pytest


def _seed_two_campaigns(cal_db):
    """Old campaign with one approved KOL; new campaign re-discovers the same
    KOL plus a fresh one."""
    cal_db.upsert_campaign_config(campaign_id="OLD", label="Old")
    cal_db.upsert_campaign_config(campaign_id="NEW", label="New")

    approved = cal_db.upsert_identity(primary_handle="@approved.kol", platform="instagram")
    fresh = cal_db.upsert_identity(primary_handle="@fresh.kol", platform="instagram")

    cal_db.upsert_candidate(campaign_id="OLD", identity_id=approved, source="discovery")
    cal_db.select_candidates_for_outreach(
        campaign_id="OLD", identity_ids=[approved], selected_by="test:op",
    )
    # Same identity re-discovered in the new campaign + one genuinely new.
    cal_db.upsert_candidate(campaign_id="NEW", identity_id=approved, source="rediscovery")
    cal_db.upsert_candidate(campaign_id="NEW", identity_id=fresh, source="rediscovery")

    cal_db.write_event(
        identity_id=fresh,
        campaign_id="NEW",
        event_type="shortlist_ready",
        actor="test",
        env="LIVE",
    )
    return approved, fresh


def test_merge_moves_candidates_and_keeps_target_decision(cal_db):
    approved, fresh = _seed_two_campaigns(cal_db)

    out = cal_db.merge_campaigns(
        source_campaign_id="NEW", target_campaign_id="OLD", env="LIVE",
    )

    assert out["candidates_moved"] == 1
    assert out["candidates_dropped_as_duplicates"] == 1
    assert out["source_config_deleted"] == 1

    rows = cal_db.list_candidate_handles("OLD")
    by_id = {r["identity_id"]: r for r in rows}
    # Operator approval survives the merge.
    assert by_id[approved]["candidate_status"] == "selected_for_outreach"
    # Fresh discovery arrives as pending.
    assert by_id[fresh]["candidate_status"] == "discovered"
    # Source campaign is fully dissolved.
    assert cal_db.list_candidate_handles("NEW") == []

    events = cal_db.list_events(campaign_id="OLD", env="LIVE")
    assert any(e["event_type"] == "shortlist_ready" for e in events)


def test_merge_rejects_same_ids_and_missing_target(cal_db):
    cal_db.upsert_campaign_config(campaign_id="ONLY", label="Only")
    with pytest.raises(ValueError):
        cal_db.merge_campaigns(
            source_campaign_id="ONLY", target_campaign_id="ONLY", env="LIVE",
        )
    with pytest.raises(ValueError):
        cal_db.merge_campaigns(
            source_campaign_id="ONLY", target_campaign_id="GHOST", env="LIVE",
        )
