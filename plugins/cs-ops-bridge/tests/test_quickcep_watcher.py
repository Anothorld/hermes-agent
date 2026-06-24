"""Tests for QuickCEP watcher REST scope and busy-session follow-up behavior."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

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

    r = cal.enqueue_session(quickcep_session_id="2547148060574048259", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")

    fake_stdout = {
        "sessions": [
            {
                "id": "2547148060574048259",
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
            stats = qw.run_rest_reconcile_once()

    assert stats.get("skipped_busy") == 1
    assert stats.get("launched") == 0
    launch.assert_not_called()
