"""Batch loaders for GET /campaigns/{id}/lanes (kanban)."""

from __future__ import annotations


def _seed_campaign(cal, *, campaign_id: str, n: int) -> list[int]:
    cal.upsert_campaign_config(campaign_id=campaign_id, env="LIVE")
    ids = []
    for i in range(n):
        iid = cal.upsert_identity(
            primary_handle=f"@kol_{campaign_id}_{i}",
            primary_email=f"kol{i}@example.com",
            env="LIVE",
        )
        cal.upsert_candidate(
            campaign_id=campaign_id,
            identity_id=iid,
            source="test",
            env="LIVE",
        )
        cal.recompute_goals(identity_id=iid, campaign_id=campaign_id, env="LIVE")
        ids.append(iid)
    return ids


def test_batch_kanban_loaders_match_per_identity(bridge_pkg, cal_db):
    cal = cal_db
    cid = "KANBAN-BATCH-1"
    ids = _seed_campaign(cal, campaign_id=cid, n=3)

    batch_facts = cal.batch_kanban_facts(campaign_id=cid, identity_ids=ids, env="LIVE")
    batch_lanes = cal.batch_lanes_views_for_campaign(cid, env="LIVE", identity_ids=ids)
    batch_rels = cal.batch_relationship_summaries(ids)

    for iid in ids:
        assert set(batch_facts[iid].keys()).issubset(set(cal.KANBAN_FACT_KEYS))
        assert batch_lanes[iid]["commerce"] or batch_lanes[iid]["fulfillment"]
        assert cal.get_lanes_view(identity_id=iid, campaign_id=cid, env="LIVE") == batch_lanes[iid]


def test_batch_lanes_returns_every_identity(bridge_pkg, cal_db):
    cal = cal_db
    cid = "KANBAN-BATCH-2"
    ids = _seed_campaign(cal, campaign_id=cid, n=5)
    batch_lanes = cal.batch_lanes_views_for_campaign(cid, env="LIVE", identity_ids=ids)
    assert set(batch_lanes.keys()) == set(ids)
    for iid in ids:
        assert "commerce" in batch_lanes[iid]
