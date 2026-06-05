"""Regression guard uses post-approval window when markers exist."""

from __future__ import annotations

import json


def _insert_edit(cal, *, iid, dist, days_ago, env="LIVE"):
    payload = {
        "was_edited": True,
        "edit_distance": dist,
        "normalized_agent_body": "a",
        "normalized_sent_body": "b",
    }
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, 'C1', 'draft_edit_learning', 'outreach', 'commerce', 't',
                       datetime('now', ?), ?, ?)""",
            (iid, f"-{days_ago} days", json.dumps(payload), env),
        )
        conn.commit()


def _insert_approval_fact(cal, store, *, iid, days_ago=6, env="LIVE"):
    val = json.dumps({"decision": "approved", "scope": "company_style", "sample_count": 3})
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_facts
               (identity_id, campaign_id, fact_namespace, fact_key, fact_value,
                source, captured_at, env)
               VALUES (?, NULL, 'approval', ?, ?, 'test', datetime('now', ?), ?)""",
            (
                iid,
                store.STYLE_LEARNING_APPROVAL_FACT,
                val,
                f"-{days_ago} days",
                env,
            ),
        )
        conn.commit()


def test_guard_basis_after_last_approval(cal_db, bridge_pkg, monkeypatch):
    overview_mod = bridge_pkg.learning_overview
    store = bridge_pkg.learning_store
    cal = cal_db
    monkeypatch.setenv("KOL_LEARNING_CONVERGENCE_ALERT_DELTA", "0.05")
    iid = cal.upsert_identity(primary_handle="guard2_kol", env="LIVE")
    _insert_edit(cal, iid=iid, dist=0.1, days_ago=20)
    _insert_edit(cal, iid=iid, dist=0.1, days_ago=12)
    _insert_edit(cal, iid=iid, dist=0.8, days_ago=1)
    _insert_approval_fact(cal, store, iid=iid, days_ago=6)

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=3)

    alert = out.get("convergence_alert") or {}
    assert alert.get("guard_basis") == "after_last_approval"
    assert alert.get("worsening") is True
