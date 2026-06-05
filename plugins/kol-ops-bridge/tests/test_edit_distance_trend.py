"""Convergence metric: edit_distance_trend aggregation."""

from __future__ import annotations

import json


def _insert_edit(cal, *, identity_id, campaign_id, edit_distance, was_edited,
                 days_ago, goal="outreach", child_skill="kol-reply-synthesizer",
                 operator_user_id=None, env="LIVE"):
    payload = {
        "was_edited": was_edited,
        "edit_distance": edit_distance,
        "child_skill": child_skill,
        "normalized_agent_body": "A",
        "normalized_sent_body": "B",
    }
    if operator_user_id is not None:
        payload["operator_user_id"] = operator_user_id
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'draft_edit_learning', ?, 'commerce', 'test',
                       datetime('now', ?), ?, ?)""",
            (
                identity_id,
                campaign_id,
                goal,
                f"-{days_ago} days",
                json.dumps(payload),
                env,
            ),
        )
        conn.commit()


def test_trend_empty(cal_db, bridge_pkg):
    store = bridge_pkg.learning_store
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = store.edit_distance_trend(conn, env="LIVE")
    assert out["overall"]["count"] == 0
    assert out["buckets"] == []
    assert out["recent_vs_prior"]["delta"] is None


def test_trend_overall_and_filters(cal_db, bridge_pkg):
    store = bridge_pkg.learning_store
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="trend_kol", env="LIVE")
    # Older, larger edits; newer, smaller edits → improving (delta < 0).
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.6,
                 was_edited=True, days_ago=40)
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.5,
                 was_edited=True, days_ago=35)
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.1,
                 was_edited=True, days_ago=2)
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.02,
                 was_edited=False, days_ago=1, goal="compensation_negotiation")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = store.edit_distance_trend(conn, env="LIVE", days=90, bucket="week")
        goal_out = store.edit_distance_trend(
            conn, env="LIVE", goal="compensation_negotiation",
        )

    assert out["overall"]["count"] == 4
    assert out["overall"]["edited_count"] == 3
    assert 0.0 <= out["overall"]["avg_edit_distance"] <= 1.0
    assert len(out["buckets"]) >= 2
    # Recent bucket avg should be below earlier weeks (learning converging).
    assert out["recent_vs_prior"]["recent_avg"] is not None
    assert out["recent_vs_prior"]["delta"] is not None
    assert out["recent_vs_prior"]["delta"] < 0

    assert goal_out["overall"]["count"] == 1
    assert goal_out["goal"] == "compensation_negotiation"


def test_trend_window_excludes_old(cal_db, bridge_pkg):
    store = bridge_pkg.learning_store
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="trend_kol2", env="LIVE")
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.4,
                 was_edited=True, days_ago=200)
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.3,
                 was_edited=True, days_ago=5)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = store.edit_distance_trend(conn, env="LIVE", days=30)
    assert out["overall"]["count"] == 1


def test_trend_operator_filter(cal_db, bridge_pkg):
    store = bridge_pkg.learning_store
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="trend_kol3", env="LIVE")
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.4,
                 was_edited=True, days_ago=3, operator_user_id=7)
    _insert_edit(cal, identity_id=iid, campaign_id="C1", edit_distance=0.2,
                 was_edited=True, days_ago=2, operator_user_id=9)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = store.edit_distance_trend(conn, env="LIVE", operator_user_id=7)
    assert out["overall"]["count"] == 1
    assert out["operator_user_id"] == 7
