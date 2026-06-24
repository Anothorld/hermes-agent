"""Integration-style tests: enqueue → gateway launch brief."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_launch_test"


def _load_module(sub: str):
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


def test_process_run_builds_expected_gateway_body():
    gw_mod = _load_module("gateway_client")
    captured: dict = {}

    def _fake_post(*, base, api_key, body, max_attempts=4):
        captured["body"] = body
        return {"run_id": "mock-run-1"}

    client = gw_mod.GatewayClient(base="http://127.0.0.1:8643", api_key="test-key")
    with patch.object(gw_mod, "post_run_with_retry", side_effect=_fake_post):
        with patch.object(gw_mod, "drain_run_events"):
            outcome = client.start_process_run(
                quickcep_session_id="2541533288463695873",
                env="LIVE",
                message_id="msg-99",
            )
    assert outcome.run_id == "mock-run-1"
    body = captured["body"]
    assert body["session_id"] == "povison-cs:LIVE:2541533288463695873"
    assert "cs_inbound_process" in body["input"]
    assert "2541533288463695873" in body["input"]
    assert "povison-cs-orchestrator-flow" in body["instructions"] or "orchestrator" in body["instructions"]


def test_launch_for_message_enqueues_and_launches(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load_module("cal")
    qw = _load_module("quickcep_watcher")
    gw_mod = _load_module("gateway_client")

    class _FakeGw:
        def start_process_run(self, **kwargs):
            return gw_mod.LaunchOutcome(run_id="run-abc")

    monkeypatch.setattr(qw, "GatewayClient", type("G", (), {"from_env": staticmethod(lambda: _FakeGw())}))

    run_id = qw._launch_for_message(
        {
            "chatSubSessionId": "sess-100",
            "chatSessionId": "chat-1",
            "id": "mid-1",
            "email": "visitor@example.com",
            "channel": "email",
        }
    )
    assert run_id == "run-abc"
    sess = cal.get_session(quickcep_session_id="sess-100", env="LIVE")
    assert sess["status"] == "processing"


def test_launch_failure_applies_failed_handoff(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load_module("cal")
    qw = _load_module("quickcep_watcher")
    gw_mod = _load_module("gateway_client")

    class _FailGw:
        def start_process_run(self, **kwargs):
            return gw_mod.LaunchOutcome(run_id=None)

    handoffs: list = []

    def _capture_handoff(**kwargs):
        handoffs.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(qw, "GatewayClient", type("G", (), {"from_env": staticmethod(lambda: _FailGw())}))
    monkeypatch.setattr(qw, "apply_handoff", _capture_handoff)

    run_id = qw._launch_for_message(
        {
            "chatSubSessionId": "sess-fail",
            "chatSessionId": "chat-1",
            "id": "mid-1",
            "email": "visitor@example.com",
            "channel": "email",
        }
    )
    assert run_id is None
    sess = cal.get_session(quickcep_session_id="sess-fail", env="LIVE")
    assert sess["status"] == "failed"
    assert len(handoffs) == 1
    assert handoffs[0]["phase"] == "failed"


def test_launch_skipped_when_intent_not_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_module("cal")
    qw = _load_module("quickcep_watcher")

    run_id = qw._launch_for_message(
        {
            "chatSubSessionId": "sess-blocked",
            "chatSessionId": "chat-1",
            "id": "mid-1",
            "email": "visitor@example.com",
            "intentionTags": ["支付咨询"],
            "channel": "email",
        }
    )
    assert run_id is None
    assert cal.get_session(quickcep_session_id="sess-blocked", env="LIVE") is None


def test_launch_skipped_for_non_email_channel(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    cal = _load_module("cal")
    qw = _load_module("quickcep_watcher")

    run_id = qw._launch_for_message(
        {
            "chatSubSessionId": "sess-web",
            "chatSessionId": "chat-1",
            "id": "mid-1",
            "email": "visitor@example.com",
            "channel": "web",
        }
    )
    assert run_id is None
    assert cal.get_session(quickcep_session_id="sess-web", env="LIVE") is None
