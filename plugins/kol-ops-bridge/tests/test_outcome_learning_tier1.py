"""Step 5 Tier1: per-collab root-cause retrospective capture."""

from __future__ import annotations


def test_classify_outcome_class(bridge_pkg):
    o = bridge_pkg.learning_outcome
    assert o.classify_outcome_class("success") == "success"
    assert o.classify_outcome_class("deal_lost") == "failure"
    assert o.classify_outcome_class("declined") == "failure"
    assert o.classify_outcome_class("paused") == "partial"
    assert o.classify_outcome_class("") == "partial"


def _archive(cal, *, handle, campaign_id, outcome, env="LIVE"):
    iid = cal.upsert_identity(primary_handle=handle, env=env)
    cal.archive_collab(
        identity_id=iid,
        campaign_id=campaign_id,
        outcome=outcome,
        negotiation_style="hard_anchor",
        decided_by="test",
    )
    return iid


def test_analyze_one_collab_writes_event_fallback(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    # Force deterministic fallback (no LLM).
    monkeypatch.setattr(
        o.learning_llm,
        "invoke_learning_llm",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    iid = _archive(cal, handle="retro1", campaign_id="C1", outcome="deal_lost")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = o.analyze_one_collab_outcome(
            conn, identity_id=iid, campaign_id="C1", env="LIVE", outcome="deal_lost",
        )
        row = conn.execute(
            """SELECT payload_json FROM kol_conversation_events
                WHERE event_type='collab_outcome_learning' AND identity_id=?""",
            (iid,),
        ).fetchone()
    assert out["llm_used"] is False
    assert out["outcome_class"] == "failure"
    assert row is not None


def test_analyze_one_collab_with_llm(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setattr(
        o.learning_llm,
        "invoke_learning_llm",
        lambda *a, **k: (
            '{"outcome_class":"failure","root_cause_tags":["price_too_high"],'
            '"what_worked":["fast first reply"],"what_failed":["over budget"],'
            '"price_summary":"KOL quote 3x our ceiling","forward_guidance":["anchor lower earlier"]}'
        ),
    )
    iid = _archive(cal, handle="retro2", campaign_id="C2", outcome="lost")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = o.analyze_one_collab_outcome(
            conn, identity_id=iid, campaign_id="C2", env="LIVE", outcome="lost",
        )
    assert out["llm_used"] is True
    assert "price_too_high" in out["root_cause_tags"]
    assert out["forward_guidance"] == ["anchor lower earlier"]


def test_analyze_pending_skips_existing(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setattr(
        o.learning_llm, "invoke_learning_llm", lambda *a, **k: "{}",
    )
    iid = _archive(cal, handle="retro3", campaign_id="C3", outcome="success")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        first = o.analyze_pending_collab_outcomes(conn, env="LIVE")
        second = o.analyze_pending_collab_outcomes(conn, env="LIVE")
    assert first["analyzed_count"] >= 1
    assert second["analyzed_count"] == 0
    assert second["skipped_existing"] >= 1
