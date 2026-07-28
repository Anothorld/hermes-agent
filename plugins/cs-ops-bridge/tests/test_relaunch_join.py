"""Tests for the relaunch joinChat hook in plugin_api.relaunch_session_route."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_relaunch_test"


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


def _ok_join_result(session_id: str) -> dict:
    return {
        "ok": True,
        "source": "relaunch",
        "session_id": session_id,
        "result_code": 200,
        "attempts": 1,
        "error": None,
        "error_detail": None,
        "failed_step": None,
        "raw": {"action": "join_chat", "result_code": 200},
    }


def test_relaunch_calls_join_before_gateway(monkeypatch, tmp_path):
    """relaunch_session_route calls joinChat after processing, before launch."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    cal = _load("cal")
    _load("email_channel")
    _load("escalation_resume")
    _load("gateway_client")
    qj = _load("quickcep_join")
    api = _load("plugin_api")

    cal.enqueue_session(quickcep_session_id="s-rel", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=1, status="failed")

    call_order: list[str] = []

    def fake_join(session_id, *, max_attempts=1, raise_on_failure=False, source="relaunch"):
        call_order.append(f"join:{session_id}")
        return _ok_join_result(session_id)

    class FakeGW:
        def start_process_run(self, **kw):
            call_order.append(f"launch:{kw['quickcep_session_id']}")
            return MagicMock(run_id="run-rel-1", dedup_skipped=False)

    # Patch the source modules (relaunch_session_route does local imports).
    ec = sys.modules[f"{_PKG}.email_channel"]
    er = sys.modules[f"{_PKG}.escalation_resume"]
    gw_mod = sys.modules[f"{_PKG}.gateway_client"]

    with patch.object(api, "_require_bridge_key"), \
         patch.object(ec, "session_is_email", return_value=True), \
         patch.object(er, "retry_resume_for_session", return_value={"kind": "none"}), \
         patch.object(qj, "join_chat_session", side_effect=fake_join) as mock_join, \
         patch.object(gw_mod, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = FakeGW()
        result = api.relaunch_session_route(
            "s-rel",
            MagicMock(env="LIVE", message_id="m-rel"),
        )

    assert result["ok"] is True
    assert result["run_id"] == "run-rel-1"
    assert call_order[0] == "join:s-rel"
    assert call_order[1] == "launch:s-rel"
    mock_join.assert_called_once()
    assert mock_join.call_args.kwargs["source"] == "relaunch"
    assert mock_join.call_args.kwargs["raise_on_failure"] is False
    # CAL has the join event
    ctx = cal.get_dispatch_context(quickcep_session_id="s-rel", env="LIVE") or {}
    types = [e["event_type"] for e in ctx.get("recent_events", [])]
    assert "quickcep_join_chat" in types


def test_relaunch_join_failure_still_launches(monkeypatch, tmp_path):
    """joinChat failure must not block the relaunch launch."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    cal = _load("cal")
    _load("email_channel")
    _load("escalation_resume")
    _load("gateway_client")
    qj = _load("quickcep_join")
    api = _load("plugin_api")

    cal.enqueue_session(quickcep_session_id="s-rel-fail", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=1, status="failed")

    launched = {"did": False}

    class FakeGW:
        def start_process_run(self, **kw):
            launched["did"] = True
            return MagicMock(run_id="run-rel-2", dedup_skipped=False)

    fail_result = {
        "ok": False,
        "source": "relaunch",
        "session_id": "s-rel-fail",
        "result_code": None,
        "attempts": 1,
        "error": "timed out",
        "error_detail": "joinChat timed out (QuickCEP HTTP)",
        "failed_step": "joinChat",
        "max_attempts": 1,
    }

    ec = sys.modules[f"{_PKG}.email_channel"]
    er = sys.modules[f"{_PKG}.escalation_resume"]
    gw_mod = sys.modules[f"{_PKG}.gateway_client"]

    with patch.object(api, "_require_bridge_key"), \
         patch.object(ec, "session_is_email", return_value=True), \
         patch.object(er, "retry_resume_for_session", return_value={"kind": "none"}), \
         patch.object(qj, "join_chat_session", return_value=fail_result), \
         patch.object(gw_mod, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = FakeGW()
        result = api.relaunch_session_route(
            "s-rel-fail",
            MagicMock(env="LIVE", message_id="m-rel"),
        )

    assert result["ok"] is True
    assert launched["did"] is True


def test_relaunch_join_disabled_by_env(monkeypatch, tmp_path):
    """CS_OPS_JOIN_CHAT_ON_LAUNCH=0 skips join on relaunch."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    monkeypatch.setenv("CS_OPS_JOIN_CHAT_ON_LAUNCH", "0")
    cal = _load("cal")
    _load("email_channel")
    _load("escalation_resume")
    _load("gateway_client")
    qj = _load("quickcep_join")
    api = _load("plugin_api")

    cal.enqueue_session(quickcep_session_id="s-rel-no-join", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=1, status="failed")

    class FakeGW:
        def start_process_run(self, **kw):
            return MagicMock(run_id="run-rel-3", dedup_skipped=False)

    ec = sys.modules[f"{_PKG}.email_channel"]
    er = sys.modules[f"{_PKG}.escalation_resume"]
    gw_mod = sys.modules[f"{_PKG}.gateway_client"]

    with patch.object(api, "_require_bridge_key"), \
         patch.object(ec, "session_is_email", return_value=True), \
         patch.object(er, "retry_resume_for_session", return_value={"kind": "none"}), \
         patch.object(qj, "join_chat_session") as mock_join, \
         patch.object(gw_mod, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = FakeGW()
        result = api.relaunch_session_route(
            "s-rel-no-join",
            MagicMock(env="LIVE", message_id="m-rel"),
        )

    assert result["ok"] is True
    assert result["run_id"] == "run-rel-3"
    mock_join.assert_not_called()


def test_relaunch_transient_requeues_to_pending(monkeypatch, tmp_path):
    """Manual relaunch on a transient (429/5xx) gateway failure re-queues to pending, not failed."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    cal = _load("cal")
    _load("email_channel")
    _load("escalation_resume")
    gw_mod = _load("gateway_client")
    qj = _load("quickcep_join")
    api = _load("plugin_api")

    r = cal.enqueue_session(quickcep_session_id="s-rel-429", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="failed")

    class _TransientGw:
        def start_process_run(self, **kw):
            return gw_mod.LaunchOutcome(run_id=None, transient=True)

    ec = sys.modules[f"{_PKG}.email_channel"]
    er = sys.modules[f"{_PKG}.escalation_resume"]

    with patch.object(api, "_require_bridge_key"), \
         patch.object(ec, "session_is_email", return_value=True), \
         patch.object(er, "retry_resume_for_session", return_value={"kind": "none"}), \
         patch.object(qj, "join_chat_session", return_value=_ok_join_result("s-rel-429")), \
         patch.object(gw_mod, "GatewayClient") as mock_gw_cls:
        mock_gw_cls.from_env.return_value = _TransientGw()
        with pytest.raises(api.HTTPException) as exc_info:
            api.relaunch_session_route(
                "s-rel-429",
                MagicMock(env="LIVE", message_id="m-rel"),
            )

    assert exc_info.value.status_code == 503
    sess = cal.get_session(quickcep_session_id="s-rel-429", env="LIVE")
    assert sess["status"] == "pending"
