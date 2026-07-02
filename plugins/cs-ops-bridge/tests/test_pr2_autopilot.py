"""Tests for PR2: Autopilot mode (default OFF, countdown, lock, cancel, tick)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr2_test"


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
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "calpr2.db"))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


@pytest.fixture()
def ap(cal):
    return _load_pkg_module("autopilot")


def _seed_draft_ready(cal, sid="qc-auto"):
    cal.enqueue_session(quickcep_session_id=sid, customer_email="a@b.com", message_id="m1")
    sess = cal.get_session(quickcep_session_id=sid)
    cal.update_session_status(session_row_id=sess["id"], status="draft_ready")
    cal.save_draft(quickcep_session_id=sid, draft_html="<p>auto reply</p>", source="agent")
    return sess


def test_autopilot_default_off(ap):
    assert ap.is_enabled() is False
    assert ap.get_settings()["autopilot_enabled"] is False


def test_on_draft_ready_noop_when_disabled(ap, cal):
    _seed_draft_ready(cal)
    job = ap.on_draft_ready(quickcep_session_id="qc-auto")
    assert job is None
    assert cal.get_latest_autopilot_job(quickcep_session_id="qc-auto") is None


def test_on_draft_ready_schedules_when_enabled(ap, cal):
    _seed_draft_ready(cal)
    ap.update_settings(enabled=True, send_after_sec=120)
    job = ap.on_draft_ready(quickcep_session_id="qc-auto")
    assert job is not None
    assert job["status"] == "scheduled"
    # send_at is in the future (~now + 120s)
    send_at = datetime.fromisoformat(job["send_at"])
    assert send_at > datetime.now(timezone.utc) - timedelta(seconds=5)
    # baseline hash recorded
    assert len(job["baseline_hash"]) == 16


def test_scheduled_job_locks_draft(ap, cal):
    _seed_draft_ready(cal)
    ap.update_settings(enabled=True)
    ap.on_draft_ready(quickcep_session_id="qc-auto")
    sess = cal.get_session(quickcep_session_id="qc-auto")
    reason = ap.autopilot_lock_check(sess)
    assert reason is not None
    assert "autopilot" in reason
    # save_draft with lock_check must refuse.
    res = cal.save_draft(
        quickcep_session_id="qc-auto", draft_html="<p>edited</p>",
        source="operator_edit", lock_check=ap.autopilot_lock_check,
    )
    assert res["success"] is False
    assert res["error"] == "draft_locked_autopilot"


def test_cancel_unlocks_draft(ap, cal):
    _seed_draft_ready(cal)
    ap.update_settings(enabled=True)
    ap.on_draft_ready(quickcep_session_id="qc-auto")
    res = ap.cancel_session_autopilot(quickcep_session_id="qc-auto")
    assert res["ok"] is True
    assert res["status"] == "cancelled"
    # Draft now editable.
    sess = cal.get_session(quickcep_session_id="qc-auto")
    assert ap.autopilot_lock_check(sess) is None
    res2 = cal.save_draft(
        quickcep_session_id="qc-auto", draft_html="<p>edited after cancel</p>",
        source="operator_edit", lock_check=ap.autopilot_lock_check,
    )
    assert res2["success"] is True


def test_tick_sends_due_job(ap, cal, monkeypatch):
    _seed_draft_ready(cal)
    ap.update_settings(enabled=True, send_after_sec=1)
    ap.on_draft_ready(quickcep_session_id="qc-auto")
    # Force send_at into the past so the job is immediately due.
    job = cal.get_latest_autopilot_job(quickcep_session_id="qc-auto")
    with cal._connect() as conn:
        conn.execute(
            "UPDATE cs_autopilot_jobs SET send_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), job["id"]),
        )
        conn.commit()
    # Stub send_reply so no real QuickCEP call.
    send_mod = _load_pkg_module("send_reply")
    monkeypatch.setattr(
        send_mod, "send_reply",
        lambda *, quickcep_session_id, env="LIVE", operator_id=None, operator_name=None, subject_override=None: {
            "ok": True, "message_id": "auto-msg-1", "session_id": quickcep_session_id,
        },
    )
    result = ap.run_autopilot_tick()
    assert result["enabled"] is True
    assert result["sent"] == 1
    job_after = cal.get_latest_autopilot_job(quickcep_session_id="qc-auto")
    assert job_after["status"] == "sent"


def test_tick_cancels_on_baseline_mismatch(ap, cal, monkeypatch):
    """If the draft changed since scheduling, the job is cancelled (not sent)."""
    _seed_draft_ready(cal)
    ap.update_settings(enabled=True, send_after_sec=1)
    ap.on_draft_ready(quickcep_session_id="qc-auto")
    # Simulate an out-of-band draft edit (bypassing lock) that changes the hash.
    sess = cal.get_session(quickcep_session_id="qc-auto")
    with cal._connect() as conn:
        conn.execute(
            "UPDATE cs_session SET draft_html=? WHERE id=?",
            ("<p>secretly edited</p>", sess["id"]),
        )
        # make job due
        conn.execute(
            "UPDATE cs_autopilot_jobs SET send_at=? WHERE session_id=?",
            (datetime.now(timezone.utc).isoformat(), sess["id"]),
        )
        conn.commit()
    send_mod = _load_pkg_module("send_reply")
    called = {"n": 0}
    monkeypatch.setattr(
        send_mod, "send_reply",
        lambda **kw: called.__setitem__("n", called["n"] + 1) or {"ok": True},
    )
    result = ap.run_autopilot_tick()
    assert result["cancelled"] == 1
    assert result["sent"] == 0
    assert called["n"] == 0  # send never called
    job_after = cal.get_latest_autopilot_job(quickcep_session_id="qc-auto")
    assert job_after["status"] == "cancelled"


def test_tick_noop_when_disabled(ap, cal):
    _seed_draft_ready(cal)
    # autopilot disabled (default) — even a due job shouldn't be claimed/sent.
    ap.update_settings(enabled=False)
    result = ap.run_autopilot_tick()
    assert result["enabled"] is False
    assert result["sent"] == 0


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "calpr2api.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="qc-api", customer_email="a@b.com", message_id="m1")
    plugin_api = _load_pkg_module("plugin_api")
    app = FastAPI()
    app.include_router(plugin_api.router)
    return app


def _h():
    return {"X-Bridge-Key": "test-key"}


def test_settings_endpoints(app):
    client = TestClient(app)
    r = client.get("/autopilot/settings", headers=_h())
    assert r.status_code == 200
    assert r.json()["autopilot_enabled"] is False
    r = client.put("/autopilot/settings", headers=_h(),
                   json={"enabled": True, "send_after_sec": 90})
    assert r.status_code == 200
    assert r.json()["autopilot_enabled"] is True
    assert r.json()["autopilot_send_after_sec"] == 90


def test_session_autopilot_endpoint(app):
    client = TestClient(app)
    r = client.get("/sessions/qc-api/autopilot", headers=_h())
    assert r.status_code == 200
    assert r.json()["autopilot"] is None


def test_cancel_endpoint_no_job_404(app):
    client = TestClient(app)
    r = client.post("/sessions/qc-api/autopilot/cancel", headers=_h())
    assert r.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
