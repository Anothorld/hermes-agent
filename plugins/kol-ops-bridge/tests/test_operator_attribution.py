"""Operator attribution for edit learning + per-operator user_style proposals."""

from __future__ import annotations

import json


def test_payload_carries_operator_user_id(bridge_pkg):
    rd = bridge_pkg.reply_diff
    payload = rd.build_edit_learning_payload(
        agent_body="Hi there, hope you are well.",
        sent_body="Hey! Hope you're doing great.",
        child_skill="kol-cold-outreach",
        goal="outreach",
        operator_user_id=7,
    )
    assert payload["operator_user_id"] == 7

    # Zero / None must not write the field.
    payload_none = rd.build_edit_learning_payload(
        agent_body="A long enough agent body here",
        sent_body="A long enough but different sent body here",
        operator_user_id=None,
    )
    assert "operator_user_id" not in payload_none


def _insert_edit(cal, *, identity_id, campaign_id, operator_user_id, env="LIVE"):
    payload = {
        "was_edited": True,
        "edit_distance": 0.3,
        "child_skill": "kol-reply-synthesizer",
        "normalized_agent_body": "A",
        "normalized_sent_body": "B",
        "operator_user_id": operator_user_id,
    }
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'draft_edit_learning', 'outreach', 'commerce', 'test', datetime('now'), ?, ?)""",
            (identity_id, campaign_id, json.dumps(payload), env),
        )
        conn.commit()


def test_list_edit_operator_ids(cal_db, bridge_pkg):
    cal = cal_db
    distill = bridge_pkg.learning_distill
    i1 = cal.upsert_identity(primary_handle="op_kol1", env="LIVE")
    i2 = cal.upsert_identity(primary_handle="op_kol2", env="LIVE")
    _insert_edit(cal, identity_id=i1, campaign_id="C1", operator_user_id=7)
    _insert_edit(cal, identity_id=i2, campaign_id="C1", operator_user_id=9)
    _insert_edit(cal, identity_id=i2, campaign_id="C2", operator_user_id=7)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        ops = distill.list_edit_operator_ids(conn, env="LIVE")
    assert set(ops) == {7, 9}


def test_user_style_proposal_filters_by_operator(cal_db, bridge_pkg, monkeypatch):
    cal = cal_db
    distill = bridge_pkg.learning_distill
    store = bridge_pkg.learning_store
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "2")

    i1 = cal.upsert_identity(primary_handle="op_a_kol", env="LIVE")
    i2 = cal.upsert_identity(primary_handle="op_b_kol", env="LIVE")
    # Operator 7: 2 edits → meets threshold. Operator 9: 1 edit.
    _insert_edit(cal, identity_id=i1, campaign_id="C1", operator_user_id=7)
    _insert_edit(cal, identity_id=i2, campaign_id="C2", operator_user_id=7)
    _insert_edit(cal, identity_id=i2, campaign_id="C3", operator_user_id=9)

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out7 = distill.propose_style_learning_approval(
            conn, env="LIVE", scope="user_style", updated_by="t", owner_user_id=7,
        )
    assert out7.get("pending") is True
    assert out7.get("sample_count") == 2
    assert out7.get("sample_operator_ids") == [7]

    # Operator 9 only has 1 attributed edit → below threshold of 2.
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out9 = distill.propose_style_learning_approval(
            conn, env="LIVE", scope="user_style", updated_by="t", owner_user_id=9,
        )
    assert out9.get("skipped") is True
    assert out9.get("reason") == "below_style_learning_batch_threshold"


def test_user_style_job_iterates_operators(cal_db, bridge_pkg, monkeypatch):
    cal = cal_db
    jobs = bridge_pkg.learning_jobs
    monkeypatch.delenv("KOL_LEARNING_USER_STYLE_OWNER_ID", raising=False)
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "1")

    i1 = cal.upsert_identity(primary_handle="job_kol1", env="LIVE")
    i2 = cal.upsert_identity(primary_handle="job_kol2", env="LIVE")
    _insert_edit(cal, identity_id=i1, campaign_id="C1", operator_user_id=7)
    _insert_edit(cal, identity_id=i2, campaign_id="C2", operator_user_id=9)

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = jobs._execute_job(
            conn,
            job_name=jobs.JOB_APPLY_EDIT_USER_STYLE,
            env="LIVE",
            triggered_by="test",
            limit=200,
            lookback_days=7,
            max_results=100,
            min_pricing_samples=3,
            dry_run=False,
        )
    assert out.get("owner_count") == 2
    assert out.get("proposed_count") == 2
