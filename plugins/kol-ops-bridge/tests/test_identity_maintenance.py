"""Tests for legacy event payload sync and identity merge maintenance."""

from __future__ import annotations

import json

import pytest


def _seed_legacy_event(cal, *, iid: int, outcome: str = "incomplete") -> int:
    with cal._connect() as conn:  # noqa: SLF001
        conn.execute(
            """INSERT INTO kol_conversation_events
               (env, identity_id, campaign_id, event_type, actor, goal, lane,
                payload_json, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "LIVE",
                iid,
                "legacy-redlist-test",
                "legacy.collab_imported",
                "test",
                "post_collab_archival",
                "meta",
                json.dumps({
                    "outcome": outcome,
                    "source_section": "红人日报表",
                    "product": "SKU1",
                }),
                "2026-05-26T07:03:13+00:00",
            ),
        )
        eid = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        conn.commit()
    return eid


def test_sync_stale_legacy_event_payloads(cal_db, bridge_pkg):
    cal = cal_db
    lor = bridge_pkg.legacy_outcome_repair
    iid = cal.upsert_identity(primary_handle="sync_evt@test", env="LIVE")
    assert iid is not None
    cal.upsert_relationship(
        identity_id=iid,
        last_outcome="success",
        increment_collabs=True,
        last_campaign_id="legacy-redlist-test",
    )
    eid = _seed_legacy_event(cal, iid=iid, outcome="incomplete")
    out = lor.sync_stale_legacy_event_payloads(env="LIVE", dry_run=False)
    assert out["events_updated"] >= 1
    with cal._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT payload_json FROM kol_conversation_events WHERE id=?",
            (eid,),
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["outcome"] == "success"


def test_merge_identities_rejects_different_handles(cal_db, bridge_pkg):
    cal = cal_db
    im = bridge_pkg.identity_merge
    keep = cal.upsert_identity(primary_handle="merge_keep@test", env="LIVE")
    drop = cal.upsert_identity(primary_handle="merge_drop@test", env="LIVE")
    assert keep is not None and drop is not None
    with pytest.raises(ValueError, match="primary_handle"):
        im.merge_identities(keep_id=keep, merge_id=drop, env="LIVE", dry_run=False)


def test_list_duplicate_identity_groups_empty_on_fresh_db(cal_db, bridge_pkg):
    im = bridge_pkg.identity_merge
    items = im.list_duplicate_identity_groups(env="LIVE")
    assert items == []
