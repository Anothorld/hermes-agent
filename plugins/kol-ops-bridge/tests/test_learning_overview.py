"""GET /learning/overview aggregate payload."""

from __future__ import annotations


def test_build_learning_overview_shape(cal_db, bridge_pkg):
    overview_mod = bridge_pkg.learning_overview
    pol = bridge_pkg.policies
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="reply_strategy",
            content_md="## compensation_negotiation\n- tactic\n",
            updated_by="test",
            env="LIVE",
        )
        out = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=5)
    assert out["env"] == "LIVE"
    assert "edit_stats" in out
    assert out["batch_threshold"] >= 1
    assert "policy_versions" in out
    assert out["policy_versions"]["reply_strategy"]["version"] is not None
    assert len(out["promote_eligibility"]) == 4
    assert "promote_metric_note" in out
    assert "last_runs" in out


def test_overview_lists_user_style_pending(cal_db, bridge_pkg, monkeypatch):
    overview_mod = bridge_pkg.learning_overview
    distill = bridge_pkg.learning_distill
    cal = cal_db
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "1")

    iid = cal.upsert_identity(primary_handle="user_style_kol", env="LIVE")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'draft_edit_learning', 'outreach', 'commerce', 'test', datetime('now'), ?, ?)""",
            (
                iid,
                "C_US",
                '{"was_edited": true, "child_skill": "kol-reply-synthesizer", '
                '"edit_distance": 5, "normalized_agent_body": "A", '
                '"normalized_sent_body": "B"}',
                "LIVE",
            ),
        )
        conn.commit()
        distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="user_style",
            updated_by="test",
            owner_user_id=42,
            limit=50,
            batch_size=1,
        )
        out = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=5)
    user_pending = [
        p for p in out["pending_style_proposals"] if p.get("scope") == "user_style"
    ]
    assert len(user_pending) == 1
    assert user_pending[0].get("owner_user_id") == 42
