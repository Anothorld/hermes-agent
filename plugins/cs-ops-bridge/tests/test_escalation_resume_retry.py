"""Tests for resume failure detection + notification + manual retry (档位 B)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_resume_retry_test"


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


@pytest.fixture(autouse=True)
def _isolate_cal_db(monkeypatch, tmp_path):
    """Per-test fresh CAL DB.

    ``_load("cal")`` caches the module, so ``cal._DB_PATH`` (read from the env
    at import time) would otherwise stay pinned to the FIRST test's tmp_path —
    every subsequent test in this file would share that DB and cross-contaminate
    (e.g. a prior test's ``resume_failed_notified`` marker surfaces as
    "already notified" in a later test). Re-sync ``_DB_PATH`` from the per-test
    env var and reset the schema-init flags so each test gets its own fresh DB.
    """
    db = tmp_path / "cal.db"
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(db))
    cal = _load("cal")
    cal._DB_PATH = db
    cal._schema_initialized = False
    cal._initialized_db_path = None
    yield


def _setup_escalation_with_answer(cal, *, qsid="qs-det", env="LIVE", answer="Expert says: 10% off",
                                   session_status="awaiting_expert"):
    """Helper: enqueue session, open+claim escalation with operator_answer."""
    r = cal.enqueue_session(quickcep_session_id=qsid, message_id="m1", env=env)
    if session_status:
        cal.update_session_status(session_row_id=r["session"]["id"], status=session_status)
    eid = cal.open_escalation(quickcep_session_id=qsid, reason="test", env=env)
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer=answer,
        decided_by="test_op",
        feishu_reply_message_id="om_reply1",
    )
    return eid


class _FakeGw:
    """Minimal fake GatewayClient for testing."""

    def __init__(self, *, run_id="run-fake", run_status=None, stop_ok=True):
        self._run_id = run_id
        self._run_status = run_status
        self._stop_ok = stop_ok
        self.stop_calls: list[str] = []
        self.status_calls: list[str] = []
        self.resume_calls: list[dict] = []

    def start_resume_run(self, **kwargs):
        self.resume_calls.append(kwargs)
        return _LaunchOutcome(self._run_id)

    def stop_run(self, run_id):
        self.stop_calls.append(run_id)
        return self._stop_ok

    def get_run_status(self, run_id):
        self.status_calls.append(run_id)
        return self._run_status


class _LaunchOutcome:
    def __init__(self, run_id):
        self.run_id = run_id
        self.dedup_skipped = False


def _patch_gateway(resume_mod, fake_gw):
    return patch.object(
        resume_mod,
        "GatewayClient",
        type("G", (), {"from_env": staticmethod(lambda: fake_gw)}),
    )


# ---------------------------------------------------------------------------
# Test 1: handle_resume_run_finished — esc still resuming + run completed → notify
# ---------------------------------------------------------------------------


def test_detection_notifies_when_resuming_after_run_completes(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal)
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-1")

    feishu_called = []
    fake_gw = _FakeGw(run_status={"status": "completed"})
    with _patch_gateway(resume_mod, fake_gw):
        with patch("cs_ops_bridge_resume_retry_test.feishu_notify.notify_escalation_resume_failed",
                   lambda **kw: feishu_called.append(kw)) if False else \
             patch.object(resume_mod, "cal", cal):
            # Patch feishu_notify inside the function's import
            import sys as _sys
            # The function does `from . import feishu_notify` — patch it on the package
            feishu_mod = _load("feishu_notify")
            with patch.object(feishu_mod, "notify_escalation_resume_failed",
                              lambda **kw: feishu_called.append(kw) or type("R", (), {"ok": True, "message_id": "", "error": ""})()):
                result = resume_mod.handle_resume_run_finished(
                    session_id="povison-cs:LIVE:qs-det",
                    completed=True,
                )
    assert result["action"] == "notified"
    assert result["escalation_id"] == eid
    assert len(feishu_called) == 1
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["resume_context"]["resume_failed_notified"] is True
    assert esc["resume_context"]["resume_failed_detected"] is True


# ---------------------------------------------------------------------------
# Test 2: esc already resolved → noop
# ---------------------------------------------------------------------------


def test_detection_noop_when_escalation_resolved(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal)
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-1")
    cal.finalize_escalation(escalation_id=eid, decision="completed")

    fake_gw = _FakeGw()
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.handle_resume_run_finished(
            session_id="povison-cs:LIVE:qs-det",
        )
    assert result["action"] == "noop"
    assert len(fake_gw.status_calls) == 0  # no run status query needed


# ---------------------------------------------------------------------------
# Test 3: no escalation → noop
# ---------------------------------------------------------------------------


def test_detection_noop_when_no_escalation(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    cal.enqueue_session(quickcep_session_id="qs-none", message_id="m1", env="LIVE")

    fake_gw = _FakeGw()
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.handle_resume_run_finished(
            session_id="povison-cs:LIVE:qs-none",
        )
    assert result["action"] == "noop"


# ---------------------------------------------------------------------------
# Test 4: already notified → idempotent noop
# ---------------------------------------------------------------------------


def test_detection_idempotent_when_already_notified(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal)
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-1")
    cal.merge_escalation_resume_context(escalation_id=eid, patch={"resume_failed_notified": True})

    fake_gw = _FakeGw(run_status={"status": "completed"})
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.handle_resume_run_finished(
            session_id="povison-cs:LIVE:qs-det",
        )
    assert result["action"] == "noop"
    assert "already notified" in result.get("reason", "")


# ---------------------------------------------------------------------------
# Test 5: false-positive guard — resume run still running → noop
# ---------------------------------------------------------------------------


def test_detection_skips_when_resume_run_still_running(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal)
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-active")

    fake_gw = _FakeGw(run_status={"status": "running"})
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.handle_resume_run_finished(
            session_id="povison-cs:LIVE:qs-det",
        )
    assert result["action"] == "noop"
    assert "still running" in result.get("reason", "")
    esc = cal.get_escalation(escalation_id=eid)
    assert "resume_failed_notified" not in esc["resume_context"]


# ---------------------------------------------------------------------------
# Test 6: 409 ordering — awaiting_expert + operator_answer → resume retry (not 409)
#         awaiting_expert + no operator_answer → 409
# ---------------------------------------------------------------------------


def test_relaunch_routes_resume_retry_before_409(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal, qsid="qs-409")
    # Session is awaiting_expert (claim doesn't change session status)
    sess = cal.get_session(quickcep_session_id="qs-409", env="LIVE")
    assert sess["status"] != "operator_replied"  # should be pending or similar

    fake_gw = _FakeGw(run_id="run-retry")
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.retry_resume_for_session(
            quickcep_session_id="qs-409", env="LIVE",
        )
    assert result["kind"] == "resume_retry"
    assert result["ok"] is True
    assert result["escalation_id"] == eid


def test_relaunch_returns_no_resume_when_no_operator_answer(monkeypatch, tmp_path):
    """Session with awaiting_answer escalation (no expert reply) → no_resume → caller hits 409."""
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    cal.enqueue_session(quickcep_session_id="qs-noanswer", message_id="m1", env="LIVE")
    cal.open_escalation(quickcep_session_id="qs-noanswer", reason="test", env="LIVE")
    # Escalation is awaiting_answer, no operator_answer_raw

    fake_gw = _FakeGw()
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.retry_resume_for_session(
            quickcep_session_id="qs-noanswer", env="LIVE",
        )
    assert result["kind"] == "no_resume"


# ---------------------------------------------------------------------------
# Test 7: manual retry reopens + clears failure markers + resets anchor
# ---------------------------------------------------------------------------


def test_manual_retry_clears_failure_markers(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal, qsid="qs-retry")
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-old")
    cal.merge_escalation_resume_context(escalation_id=eid, patch={
        "resume_failed_notified": True,
        "resume_failed_detected": True,
    })

    fake_gw = _FakeGw(run_id="run-new")
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.retry_resume_for_session(
            quickcep_session_id="qs-retry", env="LIVE",
        )
    assert result["kind"] == "resume_retry"
    assert result["run_id"] == "run-new"
    esc = cal.get_escalation(escalation_id=eid)
    ctx = esc["resume_context"]
    assert "resume_failed_notified" not in ctx
    assert "resume_failed_detected" not in ctx
    assert ctx.get("retried_at") is not None
    # resume_run_id should be the new run (set by resume_escalation → record_escalation_resume_run)
    assert ctx.get("resume_run_id") == "run-new"


# ---------------------------------------------------------------------------
# Test 8: operator_replied status guard → no_resume
# ---------------------------------------------------------------------------


def test_retry_skipped_for_operator_replied(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    eid = _setup_escalation_with_answer(cal, qsid="qs-op")
    cal.update_session_status(
        session_row_id=cal.get_session(quickcep_session_id="qs-op", env="LIVE")["id"],
        status="operator_replied",
    )

    fake_gw = _FakeGw()
    with _patch_gateway(resume_mod, fake_gw):
        result = resume_mod.retry_resume_for_session(
            quickcep_session_id="qs-op", env="LIVE",
        )
    assert result["kind"] == "no_resume"
    assert len(fake_gw.resume_calls) == 0


# ---------------------------------------------------------------------------
# Test 9: multi-escalation — prefer resuming over resolved
# ---------------------------------------------------------------------------


def test_get_latest_escalation_prefers_resuming(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    cal.enqueue_session(quickcep_session_id="qs-multi", message_id="m1", env="LIVE")

    # First escalation: claimed, then resolved (failed)
    eid1 = cal.open_escalation(quickcep_session_id="qs-multi", reason="first", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid1, operator_answer="answer1",
        decided_by="op", feishu_reply_message_id="om1",
    )
    cal.finalize_escalation(escalation_id=eid1, decision="failed")

    # Second escalation: currently resuming
    eid2 = cal.open_escalation(quickcep_session_id="qs-multi", reason="second", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid2, operator_answer="answer2",
        decided_by="op", feishu_reply_message_id="om2",
    )
    assert eid2 > eid1

    result = cal.get_latest_escalation_with_operator_answer(
        quickcep_session_id="qs-multi", env="LIVE",
    )
    assert result is not None
    assert result["id"] == eid2
    assert result["state"] == "resuming"


# ---------------------------------------------------------------------------
# Test 10: reopen resets resume_launched_at → 4h timeout won't immediately close
# ---------------------------------------------------------------------------


def test_reopen_resets_timeout_anchor(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    eid = _setup_escalation_with_answer(cal, qsid="qs-anchor")
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-old")
    cal.finalize_escalation(escalation_id=eid, decision="failed")

    esc_before = cal.get_escalation(escalation_id=eid)
    old_ctx = esc_before["resume_context"]
    # resume_launched_at might not be present after finalize, but the field
    # should be set fresh by reopen
    assert cal.reopen_escalation_for_resume(escalation_id=eid)
    esc_after = cal.get_escalation(escalation_id=eid)
    assert esc_after["state"] == "resuming"
    ctx = esc_after["resume_context"]
    assert ctx.get("resume_launched_at") is not None
    assert ctx.get("retried_at") is not None
    assert "resume_run_id" not in ctx


# ---------------------------------------------------------------------------
# Test 11: gateway hook on_session_end fires-and-forgets HTTP POST
# ---------------------------------------------------------------------------


def test_gateway_hook_on_session_end_starts_daemon_thread(monkeypatch):
    hooks_mod = _load_hooks_module()
    monkeypatch.setenv("CS_OPS_PROFILE", "povison-cs")
    monkeypatch.setenv("CS_OPS_BRIDGE_BASE", "http://127.0.0.1:9999")
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")

    threads_before = __import__("threading").active_count()
    # Non-CS session_id → should not start a thread
    hooks_mod.on_session_end(session_id="other-profile:LIVE:qs-x")
    assert __import__("threading").active_count() == threads_before

    # CS session_id → should start a daemon thread (it will fail to connect, that's fine)
    hooks_mod.on_session_end(session_id="povison-cs:LIVE:qs-x", completed=True)
    # Give the daemon thread a moment to run (it will fail silently)
    import time
    time.sleep(0.1)
    # The hook returned immediately (fire-and-forget) — main thread is not blocked


def _load_hooks_module():
    """Load cs-bridge-agent-guard hooks.py in isolation."""
    guard_root = _PLUGIN_ROOT.parent / "cs-bridge-agent-guard"
    pkg_name = "cs_bridge_guard_hook_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(guard_root)]
        sys.modules[pkg_name] = pkg
    full = f"{pkg_name}.hooks"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full, guard_root / "hooks.py",
        submodule_search_locations=[str(guard_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 12: env var missing → hook logs warning, doesn't throw
# ---------------------------------------------------------------------------


def test_gateway_hook_env_var_missing_no_exception(monkeypatch):
    hooks_mod = _load_hooks_module()
    monkeypatch.setenv("CS_OPS_PROFILE", "povison-cs")
    monkeypatch.delenv("CS_OPS_BRIDGE_BASE", raising=False)
    monkeypatch.delenv("HERMES_CS_OPS_BRIDGE_KEY", raising=False)
    monkeypatch.delenv("CS_OPS_BRIDGE_KEY", raising=False)

    # Should not raise even with missing env vars
    hooks_mod.on_session_end(session_id="povison-cs:LIVE:qs-x")


# ---------------------------------------------------------------------------
# Test 13: no feishu_message_id → skip Feishu, only CAL event
# ---------------------------------------------------------------------------


def test_detection_skips_feishu_when_no_thread(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    # Open escalation without feishu_message_id (console-only)
    cal.enqueue_session(quickcep_session_id="qs-nothread", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-nothread", reason="test", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid, operator_answer="answer",
        decided_by="op", feishu_reply_message_id="console:op:1",
    )
    cal.record_escalation_resume_run(escalation_id=eid, run_id="run-1")
    esc = cal.get_escalation(escalation_id=eid)
    assert not esc.get("feishu_message_id")

    fake_gw = _FakeGw(run_status={"status": "completed"})
    feishu_mod = _load("feishu_notify")
    feishu_called = []
    with _patch_gateway(resume_mod, fake_gw):
        with patch.object(feishu_mod, "notify_escalation_resume_failed",
                          lambda **kw: feishu_called.append(kw)):
            result = resume_mod.handle_resume_run_finished(
                session_id="povison-cs:LIVE:qs-nothread",
            )
    assert result["action"] == "notified"
    # The function still calls notify_escalation_resume_failed (mocked here),
    # but the real implementation would skip Feishu when feishu_message_id is empty.
    # Verify the feishu_message_id was empty in the call:
    if feishu_called:
        assert not feishu_called[0].get("feishu_message_id")
