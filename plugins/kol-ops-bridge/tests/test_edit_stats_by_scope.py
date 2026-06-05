"""Per-scope edit batch progress in learning overview."""

from __future__ import annotations

import json


def _insert_edit(cal, *, iid, cid, operator_user_id=None, env="LIVE"):
    payload = {
        "was_edited": True,
        "child_skill": "kol-reply-synthesizer",
        "edit_distance": 0.2,
        "normalized_agent_body": "A",
        "normalized_sent_body": "B",
    }
    if operator_user_id is not None:
        payload["operator_user_id"] = operator_user_id
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'draft_edit_learning', 'outreach', 'commerce', 't', datetime('now'), ?, ?)""",
            (iid, cid, json.dumps(payload), env),
        )
        conn.commit()


def test_overview_edit_stats_by_scope(cal_db, bridge_pkg, monkeypatch):
    overview_mod = bridge_pkg.learning_overview
    cal = cal_db
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "2")
    iid = cal.upsert_identity(primary_handle="scope_kol", env="LIVE")
    _insert_edit(cal, iid=iid, cid="C_CO", operator_user_id=None)
    _insert_edit(cal, iid=iid, cid="C_U1", operator_user_id=42)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=3)
    by_scope = out.get("edit_stats_by_scope") or []
    scopes = {r["scope"]: r for r in by_scope}
    assert "company_style" in scopes
    assert scopes["company_style"]["edited_available"] >= 1
    user_rows = [r for r in by_scope if r["scope"] == "user_style"]
    assert any(r.get("owner_user_id") == 42 for r in user_rows)
