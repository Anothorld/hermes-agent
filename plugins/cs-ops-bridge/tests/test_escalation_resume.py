"""Tests for escalation resume helper."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_resume_test"


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


def test_failed_session_retries_on_new_message(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r1 = cal.enqueue_session(quickcep_session_id="s-fail", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="failed")
    r2 = cal.enqueue_session(quickcep_session_id="s-fail", message_id="m2", env="LIVE")
    assert r2["should_launch"] is True
    assert r2["session"]["status"] == "pending"


def test_resume_escalation_launches_before_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    gw_mod = _load("gateway_client")
    resume_mod = _load("escalation_resume")

    cal.enqueue_session(quickcep_session_id="qs-1", message_id="m1", env="LIVE")
    eid = cal.open_escalation(
        quickcep_session_id="qs-1",
        reason="VIP discount",
        env="LIVE",
        feishu_thread_id="t1",
    )
    assert eid

    class _FakeGw:
        def start_resume_run(self, **kwargs):
            return gw_mod.LaunchOutcome(run_id="run-99")

    with patch.object(resume_mod, "GatewayClient", type("G", (), {"from_env": staticmethod(lambda: _FakeGw())})):
        out = resume_mod.resume_escalation(
            escalation_id=eid,
            operator_answer="Offer 10% max",
            decided_by="test_op",
            env="LIVE",
        )
    assert out["ok"] is True
    assert out["run_id"] == "run-99"
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resolved"
    assert esc["operator_answer"] == "Offer 10% max"
