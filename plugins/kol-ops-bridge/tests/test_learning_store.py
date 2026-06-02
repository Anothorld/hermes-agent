"""Tests for learning_store helpers."""

from __future__ import annotations


def test_slice_policy_md_for_goals(bridge_pkg):
    ls = bridge_pkg.learning_store
    md = (
        "# Reply learning hints (auto-generated)\n\n"
        "## compensation_negotiation\n"
        "- Do not open with price.\n\n"
        "## outreach\n"
        "- Keep it short.\n"
    )
    out = ls.slice_policy_md_for_goals(md, ["compensation_negotiation"])
    assert "compensation_negotiation" in out
    assert "Do not open with price" in out
    assert "Keep it short" not in out


def test_fact_corrections_include_plain_manual_source(cal_db, bridge_pkg):
    iid = cal_db.upsert_identity(primary_handle="fc1", platform="instagram")
    cal_db.write_facts(
        identity_id=iid,
        campaign_id="C1",
        namespace="offer",
        facts={"offer.compensation_mode": "paid"},
        source="email:classifier",
        env="TEST",
    )
    cal_db.write_facts(
        identity_id=iid,
        campaign_id="C1",
        namespace="offer",
        facts={"offer.compensation_mode": "gifted"},
        source="manual",
        env="TEST",
    )
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        rows = bridge_pkg.learning_store.list_fact_corrections(conn, env="TEST", identity_id=iid)
    assert len(rows) == 1
    assert rows[0]["manual_source"] == "manual"


def test_policy_env_isolation(cal_db, bridge_pkg):
    pol = bridge_pkg.policies
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="reply_learning",
            content_md="TEST only",
            updated_by="test",
            env="TEST",
        )
        pol.put_policy(
            conn,
            scope="reply_learning",
            content_md="LIVE only",
            updated_by="test",
            env="LIVE",
        )
        test_row = pol.get_policy(conn, scope="reply_learning", env="TEST")
        live_row = pol.get_policy(conn, scope="reply_learning", env="LIVE")
    assert test_row["content_md"] == "TEST only"
    assert live_row["content_md"] == "LIVE only"
