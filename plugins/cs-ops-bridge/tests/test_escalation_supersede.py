"""Tests for resuming escalation superseded by operator manual reply."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_supersede_test"


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


def test_supersede_resuming_stops_gateway_and_uses_operator_outcome(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    completion = _load("escalation_completion")

    cal.enqueue_session(quickcep_session_id="qs-res", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-res", reason="test", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="expert says hi",
        decided_by="expert",
        feishu_reply_message_id="om1",
    )
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-abc")

    gateway = MagicMock()
    gateway.stop_run.return_value = True
    gw_mod = _load("gateway_client")
    gw_cls = MagicMock()
    gw_cls.from_env.return_value = gateway

    with patch.object(gw_mod, "GatewayClient", gw_cls):
        with patch.object(
            completion.feishu_notify,
            "notify_escalation_completed",
            return_value=MagicMock(ok=True, message_id="done-1"),
        ) as done_notify:
            out = completion.complete_resuming_escalation_superseded_by_operator(
                escalation_id=eid,
                quickcep_session_id="qs-res",
                operator_hint="客服已直接回复",
            )

    assert out["ok"] is True
    gateway.stop_run.assert_called_once_with("run-abc")
    done_notify.assert_called_once()
    assert done_notify.call_args.kwargs["outcome"] == "operator_manual_reply"
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resolved"
    assert esc["decision"] == "operator_manual_reply"
