"""Stage C: style proposal rejection writes feedback consumed by next distill."""

from __future__ import annotations


def test_rejection_feedback_block_built(cal_db, bridge_pkg):
    cal = cal_db
    distill = bridge_pkg.learning_distill
    iid = cal.upsert_identity(primary_handle="rej_kol", env="LIVE")
    cal.write_event(
        identity_id=iid,
        campaign_id=None,
        event_type="style_proposal_rejected",
        goal=None,
        lane="meta",
        actor="approval:op",
        payload={
            "scope": "company_style",
            "note": "too generic, needs concrete pricing cadence",
            "tags": ["tone"],
        },
        env="LIVE",
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        block = distill._recent_rejection_feedback_block(
            conn, env="LIVE", scope="company_style", owner_user_id=None,
        )
    assert "PREVIOUSLY REJECTED" in block
    assert "too generic" in block


def test_rejection_feedback_scope_filter(cal_db, bridge_pkg):
    cal = cal_db
    distill = bridge_pkg.learning_distill
    iid = cal.upsert_identity(primary_handle="rej_kol2", env="LIVE")
    cal.write_event(
        identity_id=iid,
        campaign_id=None,
        event_type="style_proposal_rejected",
        goal=None,
        lane="meta",
        actor="approval:op",
        payload={"scope": "user_style", "owner_user_id": 5, "note": "wrong closer"},
        env="LIVE",
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        # Different operator → excluded.
        block_other = distill._recent_rejection_feedback_block(
            conn, env="LIVE", scope="user_style", owner_user_id=9,
        )
        block_match = distill._recent_rejection_feedback_block(
            conn, env="LIVE", scope="user_style", owner_user_id=5,
        )
    assert block_other == ""
    assert "wrong closer" in block_match
