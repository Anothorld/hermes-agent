"""Tier2 outcome proposals use per-segment stratified thresholds."""

from __future__ import annotations


def _write_retro(cal, *, iid, campaign_id, segment, outcome_class, env="LIVE"):
    cal.write_event(
        identity_id=iid,
        campaign_id=campaign_id,
        event_type="collab_outcome_learning",
        goal=None,
        lane="meta",
        actor="test",
        payload={
            "outcome_class": outcome_class,
            "segment": segment,
            "root_cause_tags": ["price_too_high"],
            "forward_guidance": ["anchor lower"],
            "what_worked": [],
            "what_failed": [],
        },
        env=env,
    )


def test_proposal_only_uses_ready_segment(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_BATCH_SIZE", "5")
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_MIN_FAILURES", "3")
    monkeypatch.setattr(
        o.learning_llm, "invoke_learning_llm",
        lambda *a, **k: "## Approved outcome learning\n### compensation_negotiation\n- x",
    )
    iid = cal.upsert_identity(primary_handle="seg_kol", env="LIVE")
    # Segment A: 3 failures → meets min_failures.
    for i in range(3):
        _write_retro(
            cal, iid=iid, campaign_id=f"FA{i}",
            segment="compensation_negotiation", outcome_class="failure",
        )
    # Segment B: 2 successes only → below threshold.
    for i in range(2):
        _write_retro(
            cal, iid=iid, campaign_id=f"SB{i}",
            segment="interest_qualification", outcome_class="success",
        )

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = o.propose_outcome_learning_approval(conn, env="LIVE", updated_by="t")

    assert out.get("pending") is True
    assert out.get("segment") == "compensation_negotiation"
    assert out.get("sample_count") == 3
    assert len(out.get("source_event_ids") or []) == 3
