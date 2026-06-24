"""Tests for legacy daily-report outcome repair."""

from __future__ import annotations

import json

import pytest


def _seed_legacy_incomplete(cal, *, handle: str = "legacy_bad") -> tuple[int, str]:
    iid = cal.upsert_identity(primary_handle=handle, platform="instagram", env="LIVE")
    assert iid is not None
    campaign_id = f"legacy-redlist-20250820-{handle}-abc123"
    cal.upsert_relationship(
        identity_id=iid,
        last_campaign_id=campaign_id,
        last_outcome="incomplete",
        increment_collabs=True,
        preferred_skus=["SSF8030E216"],
    )
    with cal._connect() as conn:  # noqa: SLF001
        conn.execute(
            """INSERT INTO kol_conversation_events
               (env, identity_id, campaign_id, event_type, actor, goal, lane,
                payload_json, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "LIVE",
                iid,
                campaign_id,
                "legacy.collab_imported",
                "test",
                "post_collab_archival",
                "meta",
                json.dumps({
                    "outcome": "incomplete",
                    "source_section": "红人日报表",
                    "product": "SSF8030E216",
                    "skus": ["SSF8030E216"],
                    "handle": handle,
                }, ensure_ascii=False),
                "2026-05-26T07:03:13+00:00",
            ),
        )
        conn.commit()
    return int(iid), campaign_id


def test_misclassified_legacy_blocks_discovery(cal_db, bridge_pkg):
    cal = cal_db
    ds = bridge_pkg.discovery_skip
    lor = bridge_pkg.legacy_outcome_repair
    iid, _ = _seed_legacy_incomplete(cal, handle="skip_legacy_bad")
    cal.upsert_campaign_config(campaign_id="C-disc", env="LIVE")
    assert lor.is_misclassified_legacy_incomplete(identity_id=iid) is True
    assert ds.resolve_discovery_skip_reason(identity_id=iid, env="LIVE") == "success"
    with pytest.raises(ds.DiscoverySkipActive):
        cal.upsert_candidate(
            campaign_id="C-disc",
            identity_id=iid,
            source="discovery",
            env="LIVE",
        )


def test_repair_upgrades_relationship_and_event(cal_db, bridge_pkg):
    cal = cal_db
    lor = bridge_pkg.legacy_outcome_repair
    iid, campaign_id = _seed_legacy_incomplete(cal, handle="repair_me")
    out = lor.repair_identity_outcome(identity_id=iid, env="LIVE", dry_run=False)
    assert out.get("skipped") is False
    rel = cal.get_relationship(iid) or {}
    assert rel.get("last_outcome") == "success"
    history = cal.list_collab_history(iid)
    assert history
    facts = cal.latest_facts_for(identity_id=iid, campaign_id=campaign_id, env="LIVE")
    assert facts.get("approval.archival_outcome") == "success"


def test_list_discovery_skip_includes_misclassified(cal_db, bridge_pkg):
    cal = cal_db
    iid, _ = _seed_legacy_incomplete(cal, handle="listed_skip")
    items = cal.list_discovery_skip_handles(env="LIVE")
    by_handle = {row["handle"]: row["reason"] for row in items}
    assert by_handle.get("listed_skip") == "success"
