"""Tests for PR1.4: workbench/state/sessions endpoints (pure CAL, zero QC)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr14_test"


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
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal14.db"))
    # Disable bridge-key auth for tests.
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    # Seed a session + draft + classify fact + escalation.
    cal.enqueue_session(
        quickcep_session_id="qc-wb",
        customer_email="a@b.com",
        message_id="m1",
        customer_name="Alice",
        intention_tags=["物流咨询"],
        email_subject="Re: order",
    )
    sess = cal.get_session(quickcep_session_id="qc-wb")
    cal.update_session_status(session_row_id=sess["id"], status="draft_ready")
    cal.save_draft(quickcep_session_id="qc-wb", draft_html="<p>draft</p>", source="agent")
    cal.write_facts(
        quickcep_session_id="qc-wb",
        namespaces={"classify": {"category": "logistics", "route": "auto_handle"}},
    )
    cal.open_escalation(
        quickcep_session_id="qc-wb", reason="need expert", urgency="high",
        question_to_operator="refund?",
    )

    plugin_api = _load_pkg_module("plugin_api")
    app = FastAPI()
    app.include_router(plugin_api.router)
    return app


def test_sessions_list_with_counts(app):
    client = TestClient(app)
    r = client.get("/sessions", params={"with_counts": "true"})
    assert r.status_code == 200
    body = r.json()
    assert "sessions" in body
    assert body["counts"]["all"] >= 1
    assert body["counts"]["draft"] >= 1
    assert body["total"] >= 1
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert "has_more" in body


def test_workbench_aggregate_pure_cal(app):
    client = TestClient(app)
    r = client.get("/sessions/qc-wb/workbench")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["quickcep_session_id"] == "qc-wb"
    assert body["session"]["customer_name"] == "Alice"
    assert body["session"]["intention_tags"] == ["物流咨询"]
    assert body["draft"]["html"] == "<p>draft</p>"
    assert body["draft"]["source"] == "agent"
    assert body["classify"]["category"] == "logistics"
    assert body["latest_escalation"]["reason"] == "need expert"
    assert body["autopilot_job"] is None  # PR2 not landed
    assert isinstance(body["recent_events"], list)
    assert any(e["event_type"] == "draft_saved" for e in body["recent_events"])


def test_workbench_unknown_session_404(app):
    client = TestClient(app)
    r = client.get("/sessions/nope/workbench")
    assert r.status_code == 404


def test_state_lightweight_poll(app):
    client = TestClient(app)
    r = client.get("/sessions/qc-wb/state")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft_ready"
    assert body["draft_source"] == "agent"
    assert body["escalation_state"] == "awaiting_answer"
    assert body["autopilot"] is None
    # Lightweight: must not include heavy fields like draft html or events.
    assert "draft" not in body
    assert "recent_events" not in body


def test_state_unknown_session_404(app):
    client = TestClient(app)
    r = client.get("/sessions/nope/state")
    assert r.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
