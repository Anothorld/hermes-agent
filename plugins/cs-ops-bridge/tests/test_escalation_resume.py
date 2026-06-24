"""Tests for escalation resume helper."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

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


def test_claim_escalation_reply_is_first_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    cal.enqueue_session(quickcep_session_id="qs-1", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-1", reason="test", env="LIVE")
    assert cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="first",
        decided_by="op1",
        feishu_reply_message_id="om_reply1",
    )
    assert not cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="second",
        decided_by="op2",
        feishu_reply_message_id="om_reply2",
    )
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resuming"
    assert esc["operator_answer"] == "first"
    assert esc["resume_context"]["operator_answer_raw"] == "first"


def test_resume_uses_operator_answer_raw_for_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    gw_mod = _load("gateway_client")
    resume_mod = _load("escalation_resume")

    cal.enqueue_session(quickcep_session_id="qs-2", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-2", reason="VIP", env="LIVE")
    raw = "Contact alice@example.com for 10% off"
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer=raw,
        decided_by="test_op",
        feishu_reply_message_id="om_r1",
    )
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["resume_context"]["operator_answer_raw"] == raw
    assert esc["operator_answer"] != raw
    assert "***@" in esc["operator_answer"]

    captured: dict = {}

    class _FakeGw:
        def start_resume_run(self, **kwargs):
            captured.update(kwargs)
            return gw_mod.LaunchOutcome(run_id="run-raw")

    with patch.object(resume_mod, "GatewayClient", type("G", (), {"from_env": staticmethod(lambda: _FakeGw())})):
        out = resume_mod.resume_escalation(
            escalation_id=eid,
            operator_answer="ignored",
            decided_by="test_op",
            env="LIVE",
            already_claimed=True,
        )
    assert out["ok"] is True
    assert captured["operator_answer"] == raw


def test_resume_escalation_stays_resuming_until_finalize(monkeypatch, tmp_path):
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
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="Offer 10% max",
        decided_by="test_op",
        feishu_reply_message_id="om_r1",
    )

    class _FakeGw:
        def start_resume_run(self, **kwargs):
            return gw_mod.LaunchOutcome(run_id="run-99")

    with patch.object(resume_mod, "GatewayClient", type("G", (), {"from_env": staticmethod(lambda: _FakeGw())})):
        out = resume_mod.resume_escalation(
            escalation_id=eid,
            operator_answer="Offer 10% max",
            decided_by="test_op",
            env="LIVE",
            feishu_reply_message_id="om_r1",
            already_claimed=True,
        )
    assert out["ok"] is True
    assert out["run_id"] == "run-99"
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resuming"
    assert esc["resume_context"]["resume_run_id"] == "run-99"
    assert cal.finalize_escalation(escalation_id=eid)
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resolved"
