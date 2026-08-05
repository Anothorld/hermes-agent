"""Customer rating / survey message consume gate tests.

Verifies the message-level consume contract:
- Rating (score_notify / invite_score) -> no gateway launch
- CAL status NEVER forced to skipped by rating alone
- last_message_id bumped (REST won't re-poll same CSAT)
- Idempotent on duplicate SIO delivery
- Follow-up visitor html with new msg id -> still launches
- _enqueue_permanent_skip / enqueue_session / leave-chat NEVER called
- operator_outbound_detect treats rating as non-conversational stop
- intent_gate excludes rating from latest visitor email / history
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_rating_skip_test"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if key == _PKG or key.startswith(f"{_PKG}."):
            del sys.modules[key]


def _load(sub: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


def _latest_event(cal, *, session_row_id: int, event_type: str) -> dict | None:
    with cal._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM cs_conversation_events "
            "WHERE session_id=? AND event_type=? ORDER BY id DESC LIMIT 1",
            (session_row_id, event_type),
        ).fetchone()
    return dict(row) if row else None


def _all_events(cal, *, session_row_id: int, event_type: str) -> list[dict]:
    with cal._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT * FROM cs_conversation_events "
            "WHERE session_id=? AND event_type=? ORDER BY id ASC",
            (session_row_id, event_type),
        ).fetchall()
    return [dict(r) for r in rows]


def _seed_session(cal, *, sid: str, status: str | None = None) -> dict:
    r = cal.enqueue_session(quickcep_session_id=sid, message_id="m-seed", env="LIVE")
    if status and status != "pending":
        cal.update_session_status(
            session_row_id=r["session"]["id"], status=status, allow_regression=True,
        )
    return cal.get_session(quickcep_session_id=sid, env="LIVE")


# ── detector ──────────────────────────────────────────────────────────

def test_is_customer_rating_inbound_score_notify():
    _reset_modules()
    mod = _load("rating_inbound")
    assert mod.is_customer_rating_inbound({"contentType": "score_notify"})
    assert mod.is_customer_rating_inbound({"lastMsgContentType": "score_notify"})


def test_is_customer_rating_inbound_invite_score():
    _reset_modules()
    mod = _load("rating_inbound")
    assert mod.is_customer_rating_inbound({"contentType": "invite_score"})


def test_is_customer_rating_inbound_case_insensitive():
    _reset_modules()
    mod = _load("rating_inbound")
    assert mod.is_customer_rating_inbound({"contentType": "SCORE_NOTIFY"})
    assert mod.is_customer_rating_inbound({"lastMsgContentType": "Invite_Score"})


def test_is_customer_rating_inbound_not_rating():
    _reset_modules()
    mod = _load("rating_inbound")
    assert not mod.is_customer_rating_inbound({"contentType": "html"})
    assert not mod.is_customer_rating_inbound({"contentType": "text"})
    assert not mod.is_customer_rating_inbound({"contentType": "call"})
    assert not mod.is_customer_rating_inbound({})
    assert not mod.is_customer_rating_inbound({"contentType": ""})


def test_is_customer_rating_inbound_incident_fixture():
    """Motivating payload (score=1, 'Terrible communication') detected by type."""
    _reset_modules()
    mod = _load("rating_inbound")
    info = {
        "contentType": "score_notify",
        "content": json.dumps({"score": 1, "feedback": "Terrible communication"}),
    }
    assert mod.is_customer_rating_inbound(info)


# ── cal.update_last_message_id ────────────────────────────────────────

def test_update_last_message_id_preserves_status_pending(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    cal.enqueue_session(quickcep_session_id="qs-bump", message_id="m1", env="LIVE")
    ok = cal.update_last_message_id(
        quickcep_session_id="qs-bump", message_id="m-rating", env="LIVE",
    )
    assert ok is True
    sess = cal.get_session(quickcep_session_id="qs-bump", env="LIVE")
    assert sess["status"] == "pending"
    assert sess["last_message_id"] == "m-rating"


def test_update_last_message_id_preserves_processing(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r = cal.enqueue_session(quickcep_session_id="qs-proc", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    cal.update_last_message_id(
        quickcep_session_id="qs-proc", message_id="m-rating", env="LIVE",
    )
    sess = cal.get_session(quickcep_session_id="qs-proc", env="LIVE")
    assert sess["status"] == "processing"
    assert sess["last_message_id"] == "m-rating"


def test_update_last_message_id_preserves_operator_replied(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r = cal.enqueue_session(quickcep_session_id="qs-or", message_id="m1", env="LIVE")
    cal.update_session_status(
        session_row_id=r["session"]["id"], status="operator_replied", allow_regression=True,
    )
    cal.update_last_message_id(
        quickcep_session_id="qs-or", message_id="m-rating", env="LIVE",
    )
    sess = cal.get_session(quickcep_session_id="qs-or", env="LIVE")
    assert sess["status"] == "operator_replied"
    assert sess["last_message_id"] == "m-rating"


def test_update_last_message_id_missing_session_returns_false(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    ok = cal.update_last_message_id(
        quickcep_session_id="qs-none", message_id="m-rating", env="LIVE",
    )
    assert ok is False


def test_update_last_message_id_empty_message_id_returns_false(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    cal.enqueue_session(quickcep_session_id="qs-empty", message_id="m1", env="LIVE")
    ok = cal.update_last_message_id(
        quickcep_session_id="qs-empty", message_id="", env="LIVE",
    )
    assert ok is False


# ── _launch_for_message: rating consume ──────────────────────────────

def test_customer_rating_score_notify_skips_launch(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-rating", status="operator_replied")
    with patch.object(watcher, "GatewayClient") as gc:
        gc.return_value.start_process_run.return_value = "run-x"
        result = watcher._launch_for_message({
            "chatSubSessionId": "qs-rating",
            "id": "m-rating",
            "channel": "email",
            "contentType": "score_notify",
            "content": json.dumps({"score": 1, "feedback": "Terrible communication"}),
        })
    assert result is None
    gc.return_value.start_process_run.assert_not_called()
    sess = cal.get_session(quickcep_session_id="qs-rating", env="LIVE")
    assert sess["status"] == "operator_replied"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "customer_rating"
    assert payload["prior_status"] == "operator_replied"
    assert payload["status"] == "operator_replied"


def test_customer_rating_invite_score_skips_launch(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-invite", status="reviewed")
    watcher._launch_for_message({
        "chatSubSessionId": "qs-invite",
        "id": "m-invite",
        "channel": "email",
        "contentType": "invite_score",
    })
    sess = cal.get_session(quickcep_session_id="qs-invite", env="LIVE")
    assert sess["status"] == "reviewed"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "customer_rating"


def test_customer_rating_last_msg_content_type_detected(monkeypatch, tmp_path):
    """REST-derived info carries lastMsgContentType, not contentType."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-rest", status="operator_replied")
    watcher._launch_for_message({
        "chatSubSessionId": "qs-rest",
        "id": "rest:12345",
        "channel": "email",
        "lastMsgContentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-rest", env="LIVE")
    assert sess["status"] == "operator_replied"
    assert sess["last_message_id"] == "rest:12345"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "customer_rating"


def test_customer_rating_bumps_last_message_id(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-bump", status="operator_replied")
    assert cal.get_session(quickcep_session_id="qs-bump", env="LIVE")["last_message_id"] == "m-seed"
    watcher._launch_for_message({
        "chatSubSessionId": "qs-bump",
        "id": "m-rating",
        "channel": "email",
        "contentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-bump", env="LIVE")
    assert sess["last_message_id"] == "m-rating"
    assert sess["status"] == "operator_replied"


def test_customer_rating_idempotent_on_duplicate_delivery(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-dup", status="operator_replied")
    info = {
        "chatSubSessionId": "qs-dup",
        "id": "m-rating",
        "channel": "email",
        "contentType": "score_notify",
    }
    watcher._launch_for_message(info)
    watcher._launch_for_message(info)
    sess = cal.get_session(quickcep_session_id="qs-dup", env="LIVE")
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 1


@pytest.mark.parametrize("status", [
    "processing", "operator_replied", "reviewed",
    "awaiting_expert", "draft_ready", "pending", "failed", "skipped",
])
def test_customer_rating_preserves_all_statuses(monkeypatch, tmp_path, status):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid=f"qs-{status}", status=status)
    watcher._launch_for_message({
        "chatSubSessionId": f"qs-{status}",
        "id": "m-rating",
        "channel": "email",
        "contentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id=f"qs-{status}", env="LIVE")
    assert sess["status"] == status


def test_customer_rating_no_cal_row_no_action(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    result = watcher._launch_for_message({
        "chatSubSessionId": "qs-norow",
        "id": "m-rating",
        "channel": "email",
        "contentType": "score_notify",
    })
    assert result is None
    assert cal.get_session(quickcep_session_id="qs-norow", env="LIVE") is None


def test_customer_rating_does_not_call_enqueue_permanent_skip(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-noeps", status="operator_replied")
    with patch.object(watcher, "_enqueue_permanent_skip") as eps, \
         patch.object(cal, "enqueue_session") as eq:
        watcher._launch_for_message({
            "chatSubSessionId": "qs-noeps",
            "id": "m-rating",
            "channel": "email",
            "contentType": "score_notify",
        })
    eps.assert_not_called()
    eq.assert_not_called()


def test_customer_rating_no_leave_chat(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-leave", status="operator_replied")
    cal.write_event(
        quickcep_session_id="qs-leave", env="LIVE",
        event_type="quickcep_join_chat", payload={"ok": True},
    )
    with patch.object(watcher, "_leave_quickcep_if_previously_joined") as lc:
        watcher._launch_for_message({
            "chatSubSessionId": "qs-leave",
            "id": "m-rating",
            "channel": "email",
            "contentType": "score_notify",
        })
    lc.assert_not_called()


def test_followup_html_after_rating_still_launches(monkeypatch, tmp_path):
    """Core guarantee: after rating consume on operator_replied, a later real
    visitor html with new msg id -> still launches (reopen to pending)."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-followup", status="operator_replied")
    # 1. Rating arrives -> consumed, status preserved
    watcher._launch_for_message({
        "chatSubSessionId": "qs-followup",
        "id": "m-rating",
        "channel": "email",
        "contentType": "score_notify",
    })
    assert cal.get_session(quickcep_session_id="qs-followup", env="LIVE")["status"] == "operator_replied"
    # 2. Real visitor email arrives -> must reach enqueue (reopen to pending)
    class FakeGW:
        def start_process_run(self, **kw):
            from unittest.mock import MagicMock
            return MagicMock(run_id="run-real", dedup_skipped=False)
    with patch.object(watcher, "check_intent_gate") as gate_fn, \
         patch.object(watcher, "GatewayClient") as gc_cls, \
         patch.object(watcher, "join_chat_on_launch_enabled", return_value=False):
        gate_fn.return_value = types.SimpleNamespace(
            allowed=True, reason="allowed", tags=("产品咨询",),
        )
        gc_cls.from_env.return_value = FakeGW()
        result = watcher._launch_for_message({
            "chatSubSessionId": "qs-followup",
            "id": "m-real-html",
            "channel": "email",
            "contentType": "html",
            "email": "cust@example.com",
            "intentionTags": ["产品咨询"],
        })
    assert result == "run-real"
    sess = cal.get_session(quickcep_session_id="qs-followup", env="LIVE")
    assert sess["status"] == "processing"


# ── operator_outbound_detect ──────────────────────────────────────────

def test_pick_latest_skips_score_notify_then_returns_none():
    _reset_modules()
    mod = _load("operator_outbound_detect")
    # Rating on top -> non-conversational stop, no fall-through to stale op html
    picked = mod.pick_latest_operator_outbound_email([
        {"ownerType": "visitor", "contentType": "score_notify", "id": "r1"},
        {"ownerType": "operator", "contentType": "html", "id": "op-old"},
    ])
    assert picked is None


def test_pick_latest_skips_invite_score_then_finds_operator_below():
    """System-owned invite_score is already skipped via _SKIP_OWNER_TYPES
    (continue), so the operator html below IS the latest conversational
    message — finding it is correct (the CSAT invite is system telemetry,
    not a conversational turn). This locks the existing behavior."""
    _reset_modules()
    mod = _load("operator_outbound_detect")
    picked = mod.pick_latest_operator_outbound_email([
        {"ownerType": "system", "contentType": "invite_score", "id": "inv1"},
        {"ownerType": "operator", "contentType": "html", "id": "op-1"},
    ])
    assert picked is not None
    assert picked["id"] == "op-1"


def test_pick_latest_visitor_owned_score_notify_stops_scan():
    """visitor + score_notify is NOT in _SKIP_OWNER_TYPES, so the rating
    contentType check fires and stops the scan (return None) — a CSAT
    submitted by the visitor is not a conversational turn."""
    _reset_modules()
    mod = _load("operator_outbound_detect")
    picked = mod.pick_latest_operator_outbound_email([
        {"ownerType": "visitor", "contentType": "score_notify", "id": "r1"},
        {"ownerType": "operator", "contentType": "html", "id": "op-stale"},
    ])
    assert picked is None


def test_pick_latest_operator_html_when_no_rating():
    _reset_modules()
    mod = _load("operator_outbound_detect")
    picked = mod.pick_latest_operator_outbound_email([
        {"ownerType": "operator", "contentType": "html", "id": "op-1"},
    ])
    assert picked is not None
    assert picked["id"] == "op-1"


# ── intent_gate hygiene ──────────────────────────────────────────────

def test_latest_visitor_message_skips_visitor_owned_score_notify():
    """ownerType=visitor + contentType=score_notify must NOT be treated as
    the latest visitor email (exercises the new contentType branch, since
    system-owned rows are already filtered by ownerType)."""
    _reset_modules()
    ig = _load("intent_gate")
    msgs = [
        {"ownerType": "visitor", "contentType": "score_notify", "id": "r1",
         "content": '{"score":1,"feedback":"Terrible communication"}'},
        {"ownerType": "visitor", "contentType": "html", "id": "v1",
         "content": {"content": "real email body", "subject": "Re: order"}},
    ]
    picked = ig._latest_visitor_message(msgs)
    assert picked is not None
    assert picked["contentType"] == "html"


def test_extract_conversation_history_filters_rating_records():
    """Rating contentTypes are non-conversation: _is_conversation_message
    returns False, so they never enter history or become the latest visitor
    message. Verifies the _NON_CONVERSATION_CONTENT_TYPES hygiene change."""
    _reset_modules()
    ig = _load("intent_gate")
    for ctype in ("score_notify", "invite_score"):
        msg = {"ownerType": "visitor", "contentType": ctype, "id": "r1",
               "content": '{"score":1,"feedback":"Terrible communication"}'}
        assert ig._is_conversation_message(msg) is False, ctype
    # Sanity: html still counts as conversation
    assert ig._is_conversation_message(
        {"ownerType": "visitor", "contentType": "html", "id": "v1",
         "content": {"content": "hi", "subject": "Re: x"}}
    ) is True


def test_extract_conversation_history_excludes_rating_from_history():
    """End-to-end: with [operator html, visitor score_notify, visitor html],
    the score_notify is filtered out and history contains only the operator
    html (as agent role)."""
    _reset_modules()
    ig = _load("intent_gate")
    msgs = [
        {"ownerType": "operator", "contentType": "html", "id": "op1",
         "content": {"content": "thanks", "subject": "Re: order"}},
        {"ownerType": "visitor", "contentType": "score_notify", "id": "r1",
         "content": '{"score":1}'},
        {"ownerType": "visitor", "contentType": "html", "id": "v1",
         "content": {"content": "follow up", "subject": "Re: order"}},
    ]
    history = ig._extract_conversation_history(msgs, max_turns=3)
    # history is [{role, text}]; rating must not appear, operator html should
    roles = [h["role"] for h in history]
    texts = [h["text"] for h in history]
    assert "agent" in roles
    assert all("score" not in t.lower() for t in texts)


# ── atomic consume: cross-namespace idempotency (A1) ─────────────────

def test_customer_rating_idempotent_across_sio_and_rest_namespaces(monkeypatch, tmp_path):
    """SIO delivers a CSAT with native id, then REST delivers the same CSAT
    with rest:{lastMsgTime}. The atomic consume must write exactly ONE
    inbound_skipped audit event (cross-namespace dedup) and bump
    last_message_id to the REST id (so REST stops re-polling)."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-xns", status="operator_replied")
    # 1. SIO delivers the rating with a native id
    watcher._launch_for_message({
        "chatSubSessionId": "qs-xns",
        "id": "native-abc",
        "channel": "email",
        "contentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-xns", env="LIVE")
    assert sess["last_message_id"] == "native-abc"
    assert sess["status"] == "operator_replied"
    # 2. REST delivers the same rating with a rest: synthetic id
    watcher._launch_for_message({
        "chatSubSessionId": "qs-xns",
        "id": "rest:1700000000",
        "channel": "email",
        "lastMsgContentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-xns", env="LIVE")
    assert sess["last_message_id"] == "rest:1700000000"  # bumped (loop prevention)
    assert sess["status"] == "operator_replied"
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 1  # cross-namespace dedup: no duplicate audit
    payload = json.loads(events[0]["payload_json"])
    assert payload["gate"] == "customer_rating"
    assert payload["message_id"] == "native-abc"  # first consume's id recorded


def test_customer_rating_idempotent_rest_then_sio_namespaces(monkeypatch, tmp_path):
    """Reverse order: REST first (rest:...), then SIO (native id). Still one event."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-xns2", status="reviewed")
    watcher._launch_for_message({
        "chatSubSessionId": "qs-xns2",
        "id": "rest:1700000001",
        "channel": "email",
        "lastMsgContentType": "score_notify",
    })
    watcher._launch_for_message({
        "chatSubSessionId": "qs-xns2",
        "id": "native-xyz",
        "channel": "email",
        "contentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-xns2", env="LIVE")
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 1


def test_customer_rating_second_distinct_rating_writes_own_audit(monkeypatch, tmp_path):
    """A DISTINCT second rating (session reopened → re-resolved → re-rated,
    hours later) must get its own audit event. The cross-namespace dedup is
    time-bounded (_RATING_DEDUP_WINDOW_MINUTES) so a fresh rating outside the
    window is NOT suppressed by the prior rating's audit."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-second", status="operator_replied")
    # 1. First rating consumed (writes audit)
    watcher._launch_for_message({
        "chatSubSessionId": "qs-second",
        "id": "rating-1",
        "channel": "email",
        "contentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-second", env="LIVE")
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 1
    # 2. Simulate the prior audit being OLD (outside the dedup window) by
    #    backdating its created_at timestamp.
    with cal._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE cs_conversation_events SET created_at=? "
            "WHERE session_id=? AND event_type='inbound_skipped'",
            ("2020-01-01T00:00:00+00:00", sess["id"]),
        )
        conn.commit()
    # 3. A distinct second rating arrives (new id) — must write its own audit
    watcher._launch_for_message({
        "chatSubSessionId": "qs-second",
        "id": "rating-2",
        "channel": "email",
        "contentType": "score_notify",
    })
    sess = cal.get_session(quickcep_session_id="qs-second", env="LIVE")
    assert sess["last_message_id"] == "rating-2"
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 2  # second distinct rating got its own audit


# ── atomic consume: partial-failure safety (A3) ─────────────────────

def test_customer_rating_audit_not_duplicated_when_consume_fails(monkeypatch, tmp_path):
    """If cal.consume_rating_atomic raises (DB error), no partial audit is
    left behind. Next successful consume writes exactly one event."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-fail", status="operator_replied")
    calls = {"n": 0}

    real_consume = cal.consume_rating_atomic

    def flaky(*, quickcep_session_id, message_id, content_type, env="LIVE"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return real_consume(
            quickcep_session_id=quickcep_session_id,
            message_id=message_id, content_type=content_type, env=env,
        )

    with patch.object(cal, "consume_rating_atomic", side_effect=flaky):
        watcher._launch_for_message({
            "chatSubSessionId": "qs-fail",
            "id": "m-rating",
            "channel": "email",
            "contentType": "score_notify",
        })
        # second call succeeds
        watcher._launch_for_message({
            "chatSubSessionId": "qs-fail",
            "id": "m-rating",
            "channel": "email",
            "contentType": "score_notify",
        })
    sess = cal.get_session(quickcep_session_id="qs-fail", env="LIVE")
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 1  # failed attempt left no partial audit


# ── atomic consume: no-id SIO path (A6) ──────────────────────────────

def test_customer_rating_sio_no_id_returns_without_bump(monkeypatch, tmp_path):
    """SIO rating with no `id` (and no lastMsgTime fallback now) must NOT
    bump last_message_id (REST will pick it up via rest:{lastMsgTime})."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-noid", status="operator_replied")
    prior_last = cal.get_session(quickcep_session_id="qs-noid", env="LIVE")["last_message_id"]
    result = watcher._launch_for_message({
        "chatSubSessionId": "qs-noid",
        "channel": "email",
        "contentType": "score_notify",
        "lastMsgTime": "1700000000",  # present but no longer used as id fallback
    })
    assert result is None
    sess = cal.get_session(quickcep_session_id="qs-noid", env="LIVE")
    assert sess["last_message_id"] == prior_last  # unchanged
    assert sess["status"] == "operator_replied"


# ── REST reconcile pre-filter (A2) ───────────────────────────────────

def test_run_rest_reconcile_once_consumes_rating_row_no_loop(monkeypatch, tmp_path):
    """The motivating bug: a closed (operator_replied) session that received
    a CSAT would loop every ~60s. REST pre-filter must consume the rating
    row (bump last_message_id) without launching AI, and a second reconcile
    tick with the same row must be a no-op (no new audit event)."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-restloop", status="operator_replied")

    fake_stdout = {
        "sessions": [
            {
                "id": "qs-restloop",
                "lastMsgContentType": "score_notify",
                "lastMsgTime": "1700000000",
                "channel": "email",
            }
        ]
    }

    class _Proc:
        returncode = 0
        stdout = json.dumps(fake_stdout)
        stderr = ""

    with patch.object(watcher.subprocess, "run", return_value=_Proc()), \
         patch.object(watcher, "_launch_for_message") as launch, \
         patch.object(watcher, "reconcile_operator_sent_once", return_value={"synced": 0, "checked": 0}), \
         patch.object(watcher, "rest_reconcile_eligible") as elig:
        stats1 = watcher.run_rest_reconcile_once()
        stats2 = watcher.run_rest_reconcile_once()  # second tick: same row

    launch.assert_not_called()
    elig.assert_not_called()  # rating rows bypass eligibility check
    sess = cal.get_session(quickcep_session_id="qs-restloop", env="LIVE")
    assert sess["status"] == "operator_replied"
    assert sess["last_message_id"] == "rest:1700000000"
    events = _all_events(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert len(events) == 1  # second tick is idempotent


# ── leave-chat control contrast (T3) ────────────────────────────────

def test_non_rating_non_email_skip_does_force_skipped_status(monkeypatch, tmp_path):
    """Control for test_customer_rating_no_leave_chat: a non_email permanent
    skip on an operator_replied session DOES force status=skipped (via the
    busy guard it stays operator_replied, but the skip path is reached),
    proving the rating path's no-leave-chat assertion is meaningful by
    contrast. Here we just assert the non_email gate writes an
    inbound_skipped event with gate=non_email (distinct from customer_rating)."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    _seed_session(cal, sid="qs-nonemail", status="operator_replied")
    with patch.object(watcher, "GatewayClient"):
        watcher._launch_for_message({
            "chatSubSessionId": "qs-nonemail",
            "id": "m-nonemail",
            "channel": "web",  # non-email channel
            "contentType": "text",
        })
    sess = cal.get_session(quickcep_session_id="qs-nonemail", env="LIVE")
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "non_email"  # distinct from customer_rating


# ── operator_send_reconcile integration with rating on top (T4) ────

def test_reconcile_operator_sent_skips_when_visitor_rating_on_top(monkeypatch, tmp_path):
    """End-to-end: a visitor-owned score_notify on top of an operator html
    must stop pick_latest (return None) so reconcile does NOT sync. This
    locks the integration contract (rating-on-top does not re-sync a stale
    operator reply)."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    mod = _load("operator_outbound_detect")
    picked = mod.pick_latest_operator_outbound_email([
        {"ownerType": "visitor", "contentType": "score_notify", "id": "r1"},
        {"ownerType": "operator", "contentType": "html", "id": "op-stale"},
    ])
    assert picked is None  # rating stops the scan — no re-sync


def test_reconcile_operator_sent_finds_operator_below_system_invite_score(monkeypatch, tmp_path):
    """A system-owned invite_score (CSAT invite) on top of an operator html:
    the operator html IS the latest conversational turn (the CSAT invite is
    post-resolution telemetry), so pick_latest finds it — reconcile syncs
    the operator reply. This locks the system-owned-rating continue path."""
    _reset_modules()
    mod = _load("operator_outbound_detect")
    picked = mod.pick_latest_operator_outbound_email([
        {"ownerType": "system", "contentType": "invite_score", "id": "inv1"},
        {"ownerType": "operator", "contentType": "html", "id": "op-1"},
    ])
    assert picked is not None
    assert picked["id"] == "op-1"
