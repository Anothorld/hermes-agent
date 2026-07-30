"""Tests for Console close-session (QuickCEP leave-chat + reviewed handoff)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_close_test"


def _load_pkg_module(sub: str):
    if _PKG not in sys.modules:
        import types

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


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal_close.db"))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


@pytest.fixture()
def close_module(cal):
    cal.enqueue_session(
        quickcep_session_id="qc-close",
        customer_email="close@example.com",
        message_id="m-close",
        email_subject="Close me",
    )
    return _load_pkg_module("close_session")


def _stub_cli(tmp_path, monkeypatch, close_module):
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(close_module, "_quickcep_cli_path", lambda: cli)
    return cli


def test_close_session_success_and_reviewed(close_module, monkeypatch, tmp_path, cal):
    cli = _stub_cli(tmp_path, monkeypatch, close_module)
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "chat_end": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)

    with patch.object(
        close_module,
        "apply_handoff",
        return_value={"ok": True, "phase": "reviewed"},
    ) as handoff:
        result = close_module.close_session(
            quickcep_session_id="qc-close",
            operator_id="op1",
            operator_name="Arnold",
        )

    assert result["ok"] is True
    assert captured["argv"][1:4] == [str(cli), "leave-chat", "qc-close"]
    handoff.assert_called_once()
    assert handoff.call_args.kwargs["phase"] == "reviewed"
    sess = cal.get_session(quickcep_session_id="qc-close")
    assert sess["status"] == "pending"  # handoff mocked — status unchanged


def test_close_session_applies_tags_before_leave_chat(close_module, monkeypatch, tmp_path):
    """Tags must be applied BEFORE leave-chat closes the session.

    QuickCEP silently drops tag changes on sessions that have already received
    chat_end.  This test verifies the ordering: apply_handoff is called before
    subprocess.run (leave-chat).
    """
    _stub_cli(tmp_path, monkeypatch, close_module)

    call_order: list[str] = []

    def fake_run(argv, **kwargs):
        call_order.append("leave-chat")
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)

    def fake_handoff(**kwargs):
        call_order.append("apply_handoff")
        return {"ok": True, "phase": "reviewed"}

    monkeypatch.setattr(close_module, "apply_handoff", fake_handoff)

    result = close_module.close_session(
        quickcep_session_id="qc-close",
        operator_id="op1",
        operator_name="Arnold",
    )
    assert result["ok"] is True
    # apply_handoff must come before leave-chat
    assert call_order[0] == "apply_handoff"
    assert call_order[1] == "leave-chat"


def test_close_session_quickcep_failure(close_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type(
            "P",
            (),
            {
                "returncode": 2,
                "stdout": json.dumps({"ok": False, "error": "chat_end_not_confirmed"}),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    result = close_module.close_session(quickcep_session_id="qc-close")
    assert result["ok"] is False
    assert result["error"] == "quickcep_close_failed"


def test_close_session_closes_escalations_when_requested(close_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    monkeypatch.setattr(close_module, "apply_handoff", lambda **kw: {"ok": True, "phase": "reviewed"})

    closed_calls: list[dict[str, Any]] = []

    def fake_close_esc(**kwargs):
        closed_calls.append(kwargs)
        return {"ok": True, "closed": [{"escalation_id": 7, "ok": True}]}

    # Inject a fake operator_escalation_close module into the test package.
    import types

    fake_mod = types.ModuleType(f"{_PKG}.operator_escalation_close")
    fake_mod.close_escalations_on_operator_manual_reply = fake_close_esc  # type: ignore[attr-defined]
    sys.modules[f"{_PKG}.operator_escalation_close"] = fake_mod
    setattr(sys.modules[_PKG], "operator_escalation_close", fake_mod)

    result = close_module.close_session(
        quickcep_session_id="qc-close",
        operator_id="op1",
        close_escalations=True,
        note="spam",
    )
    assert result["ok"] is True
    assert result["escalations_closed"] == [{"escalation_id": 7, "ok": True}]
    assert len(closed_calls) == 1
    assert closed_calls[0]["quickcep_session_id"] == "qc-close"


def test_close_session_skips_escalations_by_default(close_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    monkeypatch.setattr(close_module, "apply_handoff", lambda **kw: {"ok": True, "phase": "reviewed"})

    import types

    fake_mod = types.ModuleType(f"{_PKG}.operator_escalation_close")
    fake_mod.close_escalations_on_operator_manual_reply = lambda **kw: pytest.fail("should not be called")  # type: ignore[attr-defined]
    sys.modules[f"{_PKG}.operator_escalation_close"] = fake_mod
    setattr(sys.modules[_PKG], "operator_escalation_close", fake_mod)

    result = close_module.close_session(quickcep_session_id="qc-close")
    assert result["ok"] is True
    assert "escalations_closed" not in result


def test_close_session_infers_close_escalations_from_spam_note(close_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    monkeypatch.setattr(close_module, "apply_handoff", lambda **kw: {"ok": True, "phase": "reviewed"})

    closed_calls: list[dict[str, Any]] = []

    def fake_close_esc(**kwargs):
        closed_calls.append(kwargs)
        return {"ok": True, "closed": [{"escalation_id": 47, "ok": True}]}

    import types

    fake_mod = types.ModuleType(f"{_PKG}.operator_escalation_close")
    fake_mod.close_escalations_on_operator_manual_reply = fake_close_esc  # type: ignore[attr-defined]
    sys.modules[f"{_PKG}.operator_escalation_close"] = fake_mod
    setattr(sys.modules[_PKG], "operator_escalation_close", fake_mod)

    result = close_module.close_session(
        quickcep_session_id="qc-close",
        close_escalations=False,
        note="主意图为垃圾/无关，操作员关闭工单",
    )
    assert result["ok"] is True
    assert result["escalations_closed"] == [{"escalation_id": 47, "ok": True}]
    assert len(closed_calls) == 1


def test_close_session_infers_close_escalations_from_out_of_scope_note(close_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    monkeypatch.setattr(close_module, "apply_handoff", lambda **kw: {"ok": True, "phase": "reviewed"})

    closed_calls: list[dict[str, Any]] = []

    def fake_close_esc(**kwargs):
        closed_calls.append(kwargs)
        return {"ok": True, "closed": [{"escalation_id": 38, "ok": True}]}

    import types

    fake_mod = types.ModuleType(f"{_PKG}.operator_escalation_close")
    fake_mod.close_escalations_on_operator_manual_reply = fake_close_esc  # type: ignore[attr-defined]
    sys.modules[f"{_PKG}.operator_escalation_close"] = fake_mod
    setattr(sys.modules[_PKG], "operator_escalation_close", fake_mod)

    result = close_module.close_session(
        quickcep_session_id="qc-close",
        close_escalations=False,
        note="主意图不在处理范围（售后问题），操作员关闭工单",
    )
    assert result["ok"] is True
    assert result["escalations_closed"] == [{"escalation_id": 38, "ok": True}]
    assert len(closed_calls) == 1


def test_close_session_records_leave_chat_event_on_success(close_module, monkeypatch, tmp_path, cal):
    """close_session must write a quickcep_leave_chat CAL event (source=console_close, ok=True) on success.

    Before this fix, ~1000+ console_close_session calls left no leave trace, so
    join/leave net accounting drifted to 1159+ stuck sessions on the AI account.
    """
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type("P", (), {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "chat_end": True}),
            "stderr": "",
        })()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    monkeypatch.setattr(close_module, "apply_handoff", lambda **kw: {"ok": True, "phase": "reviewed"})

    result = close_module.close_session(
        quickcep_session_id="qc-close",
        operator_id="op1",
        operator_name="Arnold",
    )
    assert result["ok"] is True

    # Verify the quickcep_leave_chat event was written with source=console_close, ok=True.
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-close", env="LIVE")
    events = ctx["recent_events"]
    leave_events = [e for e in events if e["event_type"] == "quickcep_leave_chat"]
    assert len(leave_events) == 1, f"expected 1 quickcep_leave_chat event, got {len(leave_events)}"
    payload = leave_events[0]["payload"]
    assert payload["source"] == "console_close"
    assert payload["ok"] is True
    assert payload["operator_id"] == "op1"
    assert payload["operator_name"] == "Arnold"

    # The console_close_session event should also note the leave was recorded.
    close_events = [e for e in events if e["event_type"] == "console_close_session"]
    assert len(close_events) == 1
    assert close_events[0]["payload"]["leave_chat_recorded"] is True


def test_close_session_records_leave_chat_event_on_failure(close_module, monkeypatch, tmp_path, cal):
    """On leave-chat failure, close_session must STILL write a quickcep_leave_chat event (ok=False) for audit.

    The event is the only trace that a leave was attempted but failed — without it
    the session looks like the AI never tried to leave.
    """
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type("P", (), {
            "returncode": 2,
            "stdout": json.dumps({"ok": False, "error": "chat_end_not_confirmed"}),
            "stderr": "",
        })()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    # apply_handoff still runs (reviewed tags before leave) — stub it so the
    # failure path isn't masked by a handoff error.
    monkeypatch.setattr(close_module, "apply_handoff", lambda **kw: {"ok": True, "phase": "reviewed"})

    result = close_module.close_session(quickcep_session_id="qc-close")
    assert result["ok"] is False
    assert result["error"] == "quickcep_close_failed"

    ctx = cal.get_dispatch_context(quickcep_session_id="qc-close", env="LIVE")
    events = ctx["recent_events"]
    leave_events = [e for e in events if e["event_type"] == "quickcep_leave_chat"]
    assert len(leave_events) == 1
    payload = leave_events[0]["payload"]
    assert payload["source"] == "console_close"
    assert payload["ok"] is False
    assert payload["error"] == "chat_end_not_confirmed"
    # No console_close_session event on failure (the close itself failed).
    close_events = [e for e in events if e["event_type"] == "console_close_session"]
    assert len(close_events) == 0

