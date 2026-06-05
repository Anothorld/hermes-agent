"""Step 4: policy version rollback + convergence guard alert."""

from __future__ import annotations

import json


def test_rollback_restores_prior_content(cal_db, bridge_pkg):
    pol = bridge_pkg.policies
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(conn, scope="company_style", content_md="v1 body", updated_by="t")
        pol.put_policy(conn, scope="company_style", content_md="v2 body", updated_by="t")
        assert pol.get_policy(conn, scope="company_style")["content_md"] == "v2 body"
        new_row = pol.rollback_policy(
            conn, scope="company_style", to_version=1, updated_by="op",
        )
        active = pol.get_policy(conn, scope="company_style")
    assert new_row["version"] == 3
    assert active["content_md"] == "v1 body"
    assert "rollback" in (active["title"] or "")


def test_rollback_unknown_version_raises(cal_db, bridge_pkg):
    pol = bridge_pkg.policies
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(conn, scope="company_style", content_md="v1", updated_by="t")
        try:
            pol.rollback_policy(conn, scope="company_style", to_version=9, updated_by="op")
            assert False, "expected ValueError"
        except ValueError:
            pass


def _insert_edit(cal, *, iid, dist, days_ago, env="LIVE"):
    payload = {"was_edited": True, "edit_distance": dist}
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, 'C1', 'draft_edit_learning', 'outreach', 'commerce', 't',
                       datetime('now', ?), ?, ?)""",
            (iid, f"-{days_ago} days", json.dumps(payload), env),
        )
        conn.commit()


def test_overview_convergence_alert_worsening(cal_db, bridge_pkg, monkeypatch):
    overview_mod = bridge_pkg.learning_overview
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="guard_kol", env="LIVE")
    # Earlier weeks low edits, most recent week high → worsening delta.
    _insert_edit(cal, iid=iid, dist=0.1, days_ago=20)
    _insert_edit(cal, iid=iid, dist=0.1, days_ago=13)
    _insert_edit(cal, iid=iid, dist=0.7, days_ago=1)
    monkeypatch.setenv("KOL_LEARNING_CONVERGENCE_ALERT_DELTA", "0.05")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=5)
    assert "convergence_alert" in out
    assert out["convergence_alert"]["worsening"] is True
