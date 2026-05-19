"""Negotiation sequence + escalation + wipe tests."""

from __future__ import annotations

import pytest


def test_negotiation_seq_increments_per_identity(cal_db):
    a = cal_db.upsert_identity(handle="a")
    b = cal_db.upsert_identity(handle="b")
    for _ in range(3):
        cal_db.record_negotiation(kol_identity_id=a, decision="counter",
                                  kol_request_amount=1000, agent_counter_amount=600)
    cal_db.record_negotiation(kol_identity_id=b, decision="accept",
                              kol_request_amount=400)

    tl_a = cal_db.list_timeline(a)
    tl_b = cal_db.list_timeline(b)
    assert [n["seq"] for n in tl_a["negotiations"]] == [1, 2, 3]
    assert [n["seq"] for n in tl_b["negotiations"]] == [1]


def test_record_escalation_shows_in_open_list(cal_db):
    kid = cal_db.upsert_identity(handle="k")
    cal_db.record_escalation(
        reason="floor_violation",
        kol_identity_id=kid,
        classifier_confidence=0.5,
        ai_recommendation="hold",
    )
    opens = cal_db.list_escalations_open()
    assert len(opens) == 1
    assert opens[0]["reason"] == "floor_violation"


def test_wipe_env_only_accepts_known_envs(cal_db):
    with pytest.raises(ValueError):
        cal_db.wipe_env("PROD")


def test_wipe_env_deletes_only_target_env(cal_db):
    live = cal_db.upsert_identity(handle="live-k", env="LIVE")
    test = cal_db.upsert_identity(handle="test-k", env="TEST")
    cal_db.record_event(kol_identity_id=live, event_type="x", actor="chat", env="LIVE")
    cal_db.record_event(kol_identity_id=test, event_type="x", actor="chat", env="TEST")

    deleted = cal_db.wipe_env("TEST")
    assert deleted["kol_identity"] == 1
    assert deleted["kol_conversation_events"] == 1

    # LIVE side untouched.
    assert cal_db.get_identity(live) is not None
    assert len(cal_db.list_recent_events(env="LIVE")) == 1
    assert len(cal_db.list_recent_events(env="TEST")) == 0


def test_safe_write_swallows_exceptions(cal_db, monkeypatch, caplog):
    """CAL writes must NEVER raise — failure policy contract."""
    def _boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cal_db, "_connect", _boom)
    with caplog.at_level("WARNING"):
        result = cal_db.upsert_identity(handle="k")
    assert result is None
    assert any("upsert_identity failed" in r.message for r in caplog.records)


def test_latest_event_id_monotonic(cal_db):
    kid = cal_db.upsert_identity(handle="k")
    assert cal_db.latest_event_id() == 0
    cal_db.record_event(kol_identity_id=kid, event_type="a", actor="chat")
    e1 = cal_db.latest_event_id()
    cal_db.record_event(kol_identity_id=kid, event_type="b", actor="chat")
    e2 = cal_db.latest_event_id()
    assert e2 > e1
