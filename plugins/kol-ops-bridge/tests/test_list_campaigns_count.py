"""list_campaigns candidate_count must match operator-visible shortlist pool."""

from __future__ import annotations


def test_list_campaigns_count_excludes_rejected_and_archived(cal_db):
    cid = "COUNT-VISIBLE"
    cal_db.upsert_campaign_config(campaign_id=cid, env="LIVE")
    ids = []
    for handle, status in (
        ("@visible_one", "discovered"),
        ("@visible_two", "selected_for_outreach"),
        ("@hidden_rejected", "rejected"),
        ("@hidden_archived", "archived"),
    ):
        iid = cal_db.upsert_identity(primary_handle=handle, env="LIVE")
        ids.append(iid)
        cal_db.upsert_candidate(
            campaign_id=cid,
            identity_id=iid,
            source="test",
            candidate_status=status,
            env="LIVE",
        )

    items = cal_db.list_campaigns(env="LIVE")
    row = next(item for item in items if item["campaign_id"] == cid)
    assert row["candidate_count"] == 2
