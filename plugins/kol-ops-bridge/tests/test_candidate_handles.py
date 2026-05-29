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