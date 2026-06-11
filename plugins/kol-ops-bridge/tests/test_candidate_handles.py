"""Tests for deterministic candidate handle projection."""

from __future__ import annotations


def test_list_candidate_handles_joins_identities(cal_db):
    campaign_id = "C-handles"
    cal_db.upsert_campaign_config(campaign_id=campaign_id, label="Handles")
    first_id = cal_db.upsert_identity(primary_handle="@Creator.One", platform="instagram")
    second_id = cal_db.upsert_identity(primary_handle="creator_two", platform="instagram")
    cal_db.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=first_id,
        source="discovery:test",
        payload={"evidence_url": "https://www.instagram.com/Creator.One/"},
    )
    cal_db.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=second_id,
        source="discovery:test",
    )

    items = cal_db.list_candidate_handles(campaign_id)

    assert [item["handle"] for item in items] == ["Creator.One", "creator_two"]
    assert items[0]["profile_url"] == "https://www.instagram.com/Creator.One/"
    assert items[1]["identity_id"] == second_id
    assert items[0]["payload"] == {
        "evidence_url": "https://www.instagram.com/Creator.One/"
    }


def test_upsert_candidate_does_not_downgrade_selected_for_outreach(cal_db):
    """Re-ingest with discovered must not undo operator approval."""
    campaign_id = "C-downgrade-guard"
    cal_db.upsert_campaign_config(campaign_id=campaign_id, label="Guard")
    identity_id = cal_db.upsert_identity(primary_handle="@approved.one", platform="instagram")
    cal_db.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=identity_id,
        source="discovery:test",
    )
    cal_db.select_candidates_for_outreach(
        campaign_id=campaign_id,
        identity_ids=[identity_id],
        selected_by="test:operator",
    )
    cal_db.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=identity_id,
        source="discovery:revisit",
        candidate_status="discovered",
        payload={"reason": "rediscover revisit"},
    )
    row = cal_db.get_candidate_for(
        identity_id=identity_id, campaign_id=campaign_id, env="LIVE",
    )
    assert row is not None
    assert row["candidate_status"] == "selected_for_outreach"
    assert row["selected_by"] == "test:operator"