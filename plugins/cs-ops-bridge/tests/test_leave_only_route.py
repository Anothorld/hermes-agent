"""Tests for POST /sessions/{id}/leave — unassign AI without closing ticket.

Covers the Console "退席不结案" button backend: idempotency (never-joined,
already-left), source propagation, 404 on unknown session, and that CAL
status is NOT changed (no reviewed / no console_close_session event).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_leave_only_test"


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
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal_leave.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    cal.enqueue_session(
        quickcep_session_id="qc-leave",
        customer_email="a@b.com",
        message_id="m1",
        customer_name="Alice",
        intention_tags=["物流咨询"],
        email_subject="Re: order",
    )
    sess = cal.get_session(quickcep_session_id="qc-leave")
    cal.update_session_status(session_row_id=sess["id"], status="skipped")
    plugin_api = _load_pkg_module("plugin_api")
    app = FastAPI()
    app.include_router(plugin_api.router)
    return app


def _hdr():
    return {"x-bridge-key": "test-key"}


def test_leave_only_never_joined_is_noop(app):
    """Session was skipped but AI never joined → {ok,skipped}, no leave-chat call,
    no CAL status change."""
    client = TestClient(app)
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli"
    ) as cli:
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["skipped"] is True
        assert "never joined" in body["reason"]
        cli.assert_not_called()
    # CAL status unchanged.
    cal = _load_pkg_module("cal")
    sess = cal.get_session(quickcep_session_id="qc-leave")
    assert sess["status"] == "skipped"


def test_leave_only_with_prior_join_calls_leave_chat_and_records_source(app):
    """AI joined before → leave-chat is called; audit event source=console_leave_only."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    sess = cal.get_session(quickcep_session_id="qc-leave")
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli",
        return_value=(0, '{"ok": true, "result_code": 0}', ""),
    ):
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert not body.get("skipped")
    # Audit event written with the console_leave_only source.
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-leave", env="LIVE")
    leave_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "quickcep_leave_chat"]
    assert leave_evs and leave_evs[0]["payload"]["source"] == "console_leave_only"
    assert leave_evs[0]["payload"]["ok"] is True
    # CAL status still skipped (no reviewed, no console_close_session).
    sess2 = cal.get_session(quickcep_session_id="qc-leave")
    assert sess2["status"] == "skipped"
    close_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "console_close_session"]
    assert not close_evs


def test_leave_only_idempotent_after_prior_leave(app):
    """A prior leave at/after the latest join → {ok,skipped}, no new leave-chat call."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_leave_chat",
        payload={"source": "failed_handoff", "ok": True},
    )
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli"
    ) as cli:
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["skipped"] is True
        assert "already left" in body["reason"]
        cli.assert_not_called()


def test_leave_only_unknown_session_404(app):
    client = TestClient(app)
    r = client.post(
        "/sessions/does-not-exist/leave",
        json={"env": "LIVE"},
        headers=_hdr(),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "session_not_found"


def test_leave_only_fail_soft_on_cli_crash(app):
    """If leave-chat CLI crashes, route returns ok=false (not 502) — fail-soft,
    matching the terminal-handoff contract."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli",
        side_effect=RuntimeError("sio down"),
    ):
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert "sio down" in body["error"]
    # Audit event still written with ok=false.
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-leave", env="LIVE")
    leave_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "quickcep_leave_chat"]
    assert leave_evs and leave_evs[0]["payload"]["ok"] is False
    assert leave_evs[0]["payload"]["source"] == "console_leave_only"


def test_leave_only_retries_after_prior_failed_leave(app):
    """C2 regression: a prior quickcep_leave_chat with ok:false must NOT block
    a manual retry — the session may still be joined in QuickCEP. The helper
    now counts only ok:true leaves as 'already left'."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    # Prior failed leave (ok:false) — e.g. a failed_handoff leave that crashed.
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_leave_chat",
        payload={"source": "failed_handoff", "ok": False, "error": "sio down"},
    )
    cli_calls = []
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli",
        return_value=(0, '{"ok": true, "result_code": 0}', ""),
    ) as cli:
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Must NOT be a no-op — leave-chat was actually called again.
        assert body["ok"] is True
        assert not body.get("skipped")
        cli.assert_called_once()
    # A new successful leave event is written (ok:true, source=console_leave_only).
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-leave", env="LIVE")
    leave_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "quickcep_leave_chat"]
    console_leaves = [e for e in leave_evs if e["payload"]["source"] == "console_leave_only"]
    assert console_leaves and console_leaves[-1]["payload"]["ok"] is True


def test_leave_only_rejects_non_leavable_status(app):
    """C1 regression: a direct API call on a processing/awaiting_expert session
    must 409 — the backend enforces the contract, not just the FE."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    # Flip the session to processing (active AI work). allow_regression because
    # the fixture seeded it as `skipped` (rank 25) and `processing` is rank 10.
    sess = cal.get_session(quickcep_session_id="qc-leave")
    cal.update_session_status(session_row_id=sess["id"], status="processing", allow_regression=True)
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli"
    ) as cli:
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "status_not_leavable"
        assert detail["status"] == "processing"
        cli.assert_not_called()
    # CAL status unchanged, no leave event written.
    sess2 = cal.get_session(quickcep_session_id="qc-leave")
    assert sess2["status"] == "processing"
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-leave", env="LIVE")
    leave_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "quickcep_leave_chat"]
    assert not leave_evs


def test_leave_only_writes_console_leave_only_audit_event(app):
    """W1 regression: operator identity + note are persisted on a dedicated
    console_leave_only CAL event (parallel to close_session's console_close)."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli",
        return_value=(0, '{"ok": true, "result_code": 0}', ""),
    ):
        r = client.post(
            "/sessions/qc-leave/leave",
            json={
                "env": "LIVE",
                "operator_id": "op-42",
                "operator_name": "Alice",
                "note": "stuck on AI after operator_sent",
            },
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-leave", env="LIVE")
    audit_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "console_leave_only"]
    assert audit_evs, "console_leave_only audit event must be written"
    p = audit_evs[-1]["payload"]
    assert p["operator_id"] == "op-42"
    assert p["operator_name"] == "Alice"
    assert p["note"] == "stuck on AI after operator_sent"
    assert p["leave_ok"] is True
    assert p["leave_skipped"] is False


def test_leave_only_audit_event_written_even_on_cli_failure(app):
    """W1 + fail-soft: the console_leave_only audit event is written even when
    leave-chat fails, recording leave_ok=false for the audit trail."""
    client = TestClient(app)
    cal = _load_pkg_module("cal")
    cal.write_event(
        quickcep_session_id="qc-leave",
        env="LIVE",
        event_type="quickcep_join_chat",
        payload={"source": "agent_launch"},
    )
    with patch(
        "cs_ops_bridge_leave_only_test.session_handoff._run_quickcep_cli",
        return_value=(1, '{"ok": false, "error": "sio timeout"}', ""),
    ):
        r = client.post(
            "/sessions/qc-leave/leave",
            json={"env": "LIVE", "operator_id": "op1", "operator_name": "Op"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is False
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-leave", env="LIVE")
    audit_evs = [e for e in ctx.get("recent_events", []) if e["event_type"] == "console_leave_only"]
    assert audit_evs
    assert audit_evs[-1]["payload"]["leave_ok"] is False
