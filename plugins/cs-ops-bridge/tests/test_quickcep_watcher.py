"""Tests for QuickCEP watcher REST scope and busy-session follow-up behavior."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_qw_test"


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


def test_rest_session_message_id_ignores_unread_suffix():
    _reset_modules()
    qw = _load("quickcep_watcher")
    row = {"id": "2547148060574048259", "lastMsgTime": "2026-06-23 14:10:29", "unreadNum": 2}
    assert qw.rest_session_message_id(row) == "rest:2026-06-23 14:10:29"


def test_is_newer_visitor_followup_rest_vs_native_id_same_message():
    """rest:{lastMsgTime} must not false-positive against QuickCEP native msg id."""
    _reset_modules()
    qw = _load("quickcep_watcher")
    # Incident shape: CAL tracked REST lastMsgTime; messages API returns native id.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="rest:2026-07-29 00:17:45",
        visitor_msg_id="2560343188000000001",
        visitor_create_time="2026-07-29 00:17:45",
    ) is False
    # Older or equal createTime is not a follow-up.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="rest:2026-07-29 00:17:45",
        visitor_msg_id="2560343188000000001",
        visitor_create_time="2026-07-28 23:00:00",
    ) is False
    # Strictly newer createTime is a real follow-up.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="rest:2026-07-29 00:17:45",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 01:00:00",
    ) is True
    # Missing createTime on REST marker → cannot prove newer (do not re-arm).
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="rest:2026-07-29 00:17:45",
        visitor_msg_id="2560343188000000001",
        visitor_create_time="",
    ) is False


def test_is_newer_visitor_followup_native_id_match():
    _reset_modules()
    qw = _load("quickcep_watcher")
    # Same native id tracked — no new follow-up.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000001",
        visitor_create_time="2026-07-29 08:17:45",
    ) is False
    # Different native id, no inbound baseline → cannot prove newer (do not re-arm).
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 09:00:00",
    ) is False
    # Different native id, visitor (UTC+8 08:17:45) is same instant as inbound (UTC 00:17:45)
    # → not strictly newer → no re-arm. This is the 3404/3612 incident shape.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 08:17:45",
        inbound_received_at="2026-07-29 00:17:45",
    ) is False
    # Visitor (UTC+8 09:00:00 = UTC 01:00:00) is newer than inbound (UTC 00:17:50).
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 09:00:00",
        inbound_received_at="2026-07-29 00:17:50",
    ) is True
    # Visitor (UTC+8 08:17:50 = UTC 00:17:50) equal to inbound (UTC 00:17:50) → not strictly newer.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 08:17:50",
        inbound_received_at="2026-07-29 00:17:50",
    ) is False
    # Visitor older than inbound (UTC+8 07:00:00 = UTC 23:00:00 previous day) → not newer.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 07:00:00",
        inbound_received_at="2026-07-29 00:17:50",
    ) is False
    # Missing createTime with baseline present → cannot prove newer.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="",
        inbound_received_at="2026-07-29 00:17:50",
    ) is False


def test_is_newer_visitor_followup_cal_iso8601_inbound_received_at():
    """CAL ``created_at`` is ISO8601 from ``cal._now()``; parser must handle it.

    Regression for the bug where ``_parse_utc_time_to_epoch`` returned ``None``
    on real CAL timestamps (``2026-07-29T00:17:50.123456+00:00``), silently
    disabling non-REST time-based re-arm in production. Existing tests used
    space-separated SQLite ``datetime()`` shapes that masked the bug.
    """
    _reset_modules()
    qw = _load("quickcep_watcher")
    # Same instant as visitor UTC+8 08:17:45 → not strictly newer → no re-arm.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 08:17:45",
        inbound_received_at="2026-07-29T00:17:45+00:00",
    ) is False
    # Visitor (UTC+8 09:00:00 = UTC 01:00:00) newer than inbound (UTC 00:17:50).
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 09:00:00",
        inbound_received_at="2026-07-29T00:17:50.481234+00:00",
    ) is True
    # Trailing 'Z' suffix form.
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 09:00:00",
        inbound_received_at="2026-07-29T00:17:50Z",
    ) is True
    # Unparseable baseline → fail closed (do not re-arm).
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 09:00:00",
        inbound_received_at="not-a-date",
    ) is False
    # Legacy SQLite datetime() shape still parses (backward compat).
    assert qw.is_newer_visitor_followup(
        cal_last_msg_id="2560343188000000001",
        visitor_msg_id="2560343188000000099",
        visitor_create_time="2026-07-29 09:00:00",
        inbound_received_at="2026-07-29 00:17:50",
    ) is True


def test_rest_reconcile_eligible_only_pending_or_failed(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    qw = _load("quickcep_watcher")

    assert qw.rest_reconcile_eligible(quickcep_session_id="new-sid") is True

    cal.enqueue_session(quickcep_session_id="s-pending", message_id="m1", env="LIVE")
    assert qw.rest_reconcile_eligible(quickcep_session_id="s-pending") is True

    r = cal.enqueue_session(quickcep_session_id="s-busy", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    assert qw.rest_reconcile_eligible(quickcep_session_id="s-busy") is False

    r2 = cal.enqueue_session(quickcep_session_id="s-expert", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r2["session"]["id"], status="awaiting_expert")
    assert qw.rest_reconcile_eligible(quickcep_session_id="s-expert") is False


def test_busy_enqueue_records_event_without_quickcep_handoff(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load("cal")
    qw = _load("quickcep_watcher")

    r1 = cal.enqueue_session(quickcep_session_id="s-busy", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")

    with patch.object(qw, "apply_handoff") as handoff:
        run_id = qw._launch_for_message(
            {
                "chatSubSessionId": "s-busy",
                "chatSessionId": "chat-1",
                "id": "m2-customer-reply",
                "email": "visitor@example.com",
                "channel": "email",
            }
        )

    assert run_id is None
    handoff.assert_not_called()
    events = cal.get_dispatch_context(quickcep_session_id="s-busy", env="LIVE") or {}
    types = [e["event_type"] for e in events.get("recent_events", [])]
    assert "customer_followup_while_busy" in types


def test_rest_reconcile_skips_processing_sessions(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load("cal")
    qw = _load("quickcep_watcher")

    # Use a unique session id to avoid interference from other tests' CAL rows.
    sid = "2547148060574048999"
    r = cal.enqueue_session(quickcep_session_id=sid, message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")

    fake_stdout = {
        "sessions": [
            {
                "id": sid,
                "lastMsgTime": "2026-06-23 14:12:49",
                "unreadNum": 2,
                "channel": "email",
                "email": "perrin.victor@outlook.com",
            }
        ]
    }

    class _Proc:
        returncode = 0
        stdout = __import__("json").dumps(fake_stdout)
        stderr = ""

    with patch.object(qw.subprocess, "run", return_value=_Proc()):
        with patch.object(qw, "_launch_for_message") as launch:
            with patch.object(qw, "reconcile_operator_sent_once", return_value={"synced": 0, "checked": 0, "skipped_already": 0}):
                stats = qw.run_rest_reconcile_once()

    assert stats.get("skipped_busy") >= 1
    assert stats.get("launched") == 0
    launch.assert_not_called()


# ── Launch joinChat tests ───────────────────────────────────────────────


def _ok_join_result(session_id: str) -> dict:
    return {
        "ok": True,
        "source": "launch",
        "session_id": session_id,
        "result_code": 200,
        "attempts": 1,
        "error": None,
        "error_detail": None,
        "failed_step": None,
        "raw": {"action": "join_chat", "result_code": 200},
    }


def _fail_join_result(session_id: str) -> dict:
    return {
        "ok": False,
        "source": "launch",
        "session_id": session_id,
        "result_code": None,
        "attempts": 1,
        "error": "timed out",
        "error_detail": "joinChat timed out (QuickCEP HTTP)",
        "failed_step": "joinChat",
        "max_attempts": 1,
    }


def test_launch_calls_join_before_gateway(monkeypatch, tmp_path):
    """joinChat is called after processing status, before start_process_run."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load("cal")
    qw = _load("quickcep_watcher")
    _load("quickcep_join")  # ensure quickcep_join is loaded for record_join_chat_event

    call_order: list[str] = []

    def fake_join(session_id, *, max_attempts=1, raise_on_failure=False, source="launch"):
        call_order.append(f"join:{session_id}")
        return _ok_join_result(session_id)

    class FakeGW:
        def start_process_run(self, **kw):
            call_order.append(f"launch:{kw['quickcep_session_id']}")
            return MagicMock(run_id="run-1", dedup_skipped=False)

    with patch.object(qw, "join_chat_session", side_effect=fake_join), \
         patch.object(qw, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = FakeGW()
        run_id = qw._launch_for_message(
            {
                "chatSubSessionId": "s-join",
                "chatSessionId": "chat-1",
                "id": "m1",
                "email": "visitor@example.com",
                "channel": "email",
            }
        )

    assert run_id == "run-1"
    assert call_order[0] == "join:s-join"
    assert call_order[1] == "launch:s-join"
    # CAL has the join event (record_join_chat_event writes for real)
    ctx = cal.get_dispatch_context(quickcep_session_id="s-join", env="LIVE") or {}
    types = [e["event_type"] for e in ctx.get("recent_events", [])]
    assert "quickcep_join_chat" in types


def test_join_failure_still_launches(monkeypatch, tmp_path):
    """joinChat failure must not block the gateway launch."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load("cal")
    qw = _load("quickcep_watcher")

    launched = {"did": False}

    class FakeGW:
        def start_process_run(self, **kw):
            launched["did"] = True
            return MagicMock(run_id="run-2", dedup_skipped=False)

    with patch.object(qw, "join_chat_session", return_value=_fail_join_result("s-fail")), \
         patch.object(qw, "record_join_chat_event") as mock_record, \
         patch.object(qw, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = FakeGW()
        run_id = qw._launch_for_message(
            {
                "chatSubSessionId": "s-fail",
                "chatSessionId": "chat-1",
                "id": "m1",
                "email": "visitor@example.com",
                "channel": "email",
            }
        )

    assert run_id == "run-2"
    assert launched["did"] is True
    # record_join_chat_event was still called (with the failure result)
    mock_record.assert_called_once()
    join_arg = mock_record.call_args.kwargs["join_result"]
    assert join_arg["ok"] is False


def test_busy_path_does_not_join(monkeypatch, tmp_path):
    """Busy follow-up must NOT trigger joinChat."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load("cal")
    qw = _load("quickcep_watcher")

    r1 = cal.enqueue_session(quickcep_session_id="s-busy-j", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")

    with patch.object(qw, "join_chat_session") as mock_join, \
         patch.object(qw, "apply_handoff"):
        qw._launch_for_message(
            {
                "chatSubSessionId": "s-busy-j",
                "chatSessionId": "chat-1",
                "id": "m2",
                "email": "visitor@example.com",
                "channel": "email",
            }
        )

    mock_join.assert_not_called()


def test_join_disabled_by_env(monkeypatch, tmp_path):
    """CS_OPS_JOIN_CHAT_ON_LAUNCH=0 skips join entirely."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    monkeypatch.setenv("CS_OPS_JOIN_CHAT_ON_LAUNCH", "0")
    cal = _load("cal")
    qw = _load("quickcep_watcher")

    class FakeGW:
        def start_process_run(self, **kw):
            return MagicMock(run_id="run-3", dedup_skipped=False)

    with patch.object(qw, "join_chat_session") as mock_join, \
         patch.object(qw, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = FakeGW()
        run_id = qw._launch_for_message(
            {
                "chatSubSessionId": "s-no-join",
                "chatSessionId": "chat-1",
                "id": "m1",
                "email": "visitor@example.com",
                "channel": "email",
            }
        )

    assert run_id == "run-3"
    mock_join.assert_not_called()
