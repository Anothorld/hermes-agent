"""PR1 instrumentation tests: is_reopen flag, launch_failed, processing_stale_recovered, via_resume."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr1_instr_test"


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


def _latest_event(cal, *, session_row_id: int, event_type: str) -> dict | None:
    with cal._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM cs_conversation_events "
            "WHERE session_id=? AND event_type=? ORDER BY id DESC LIMIT 1",
            (session_row_id, event_type),
        ).fetchone()
    return dict(row) if row else None


# ── #2 is_reopen flag ────────────────────────────────────────────────

def test_inbound_received_first_time_is_not_reopen(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    res = cal.enqueue_session(quickcep_session_id="qs-new", message_id="m1", env="LIVE")
    ev = _latest_event(cal, session_row_id=res["session"]["id"], event_type="inbound_received")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["is_reopen"] is False
    assert payload["prior_status"] is None


def test_inbound_reopen_flags_terminal_status(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r1 = cal.enqueue_session(quickcep_session_id="qs-reopen", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="draft_ready")
    # Follow-up message reopens the terminal status.
    r2 = cal.enqueue_session(quickcep_session_id="qs-reopen", message_id="m2", env="LIVE")
    assert r2["should_launch"] is True
    ev = _latest_event(cal, session_row_id=r1["session"]["id"], event_type="inbound_received")
    payload = json.loads(ev["payload_json"])
    assert payload["is_reopen"] is True
    assert payload["prior_status"] == "draft_ready"


def test_inbound_busy_followup_not_marked_reopen(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r1 = cal.enqueue_session(quickcep_session_id="qs-busy", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")
    # Follow-up while busy → customer_followup_while_busy, not inbound_received.
    r2 = cal.enqueue_session(quickcep_session_id="qs-busy", message_id="m2", env="LIVE")
    assert r2["should_launch"] is False
    ev = _latest_event(cal, session_row_id=r1["session"]["id"], event_type="customer_followup_while_busy")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    # processing is not terminal, so is_reopen must be False.
    assert payload["is_reopen"] is False
    assert payload["prior_status"] == "processing"


# ── #3 launch_failed event ───────────────────────────────────────────

def test_launch_failed_event_written_on_gateway_failure(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    # Force gateway launch to return no run_id and not dedup_skipped.
    # transient=False simulates a PERMANENT failure (non-429/5xx) → failed path.
    fake_outcome = types.SimpleNamespace(run_id=None, dedup_skipped=False, transient=False)
    with patch.object(watcher, "GatewayClient") as gw_cls, \
         patch.object(watcher, "apply_handoff", return_value={"ok": True}) as handoff:
        gw_cls.from_env.return_value.start_process_run.return_value = fake_outcome
        watcher._launch_for_message({
            "chatSubSessionId": "qs-launchfail",
            "id": "m1",
            "channel": "email",
            "email": "cust@example.com",
            "intentionTags": ["产品咨询"],
        })
    handoff.assert_called_once()
    sess = cal.get_session(quickcep_session_id="qs-launchfail", env="LIVE")
    assert sess["status"] == "failed"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="launch_failed")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["error"] == "gateway launch failed"
    assert payload["message_id"] == "m1"


# ── #4 processing_stale_recovered event ──────────────────────────────

def test_processing_stale_recovered_event(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    stale_mod = _load("processing_stale")
    monkeypatch.setattr(stale_mod, "_STALE_MIN", 5.0)

    r = cal.enqueue_session(quickcep_session_id="qs-stale-ev", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with cal._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE cs_session SET updated_at=? WHERE id=?", (old, r["session"]["id"]))
        conn.commit()

    with patch.object(stale_mod, "apply_handoff", return_value={"ok": True}):
        stale_mod.check_processing_stale_once()

    ev = _latest_event(cal, session_row_id=r["session"]["id"], event_type="processing_stale_recovered")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["elapsed_min"] >= 5.0
    assert payload["threshold_min"] == 5.0


def test_processing_stale_no_event_when_handoff_skipped(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    stale_mod = _load("processing_stale")
    monkeypatch.setattr(stale_mod, "_STALE_MIN", 5.0)

    r = cal.enqueue_session(quickcep_session_id="qs-stale-skip", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with cal._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE cs_session SET updated_at=? WHERE id=?", (old, r["session"]["id"]))
        conn.commit()

    with patch.object(stale_mod, "apply_handoff", return_value={"ok": True, "skipped": True}):
        stale_mod.check_processing_stale_once()

    ev = _latest_event(cal, session_row_id=r["session"]["id"], event_type="processing_stale_recovered")
    assert ev is None


# ── #7 via_resume flag ───────────────────────────────────────────────

def test_via_resume_false_for_first_pass_draft_ready(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    sh = _load("session_handoff")

    r = cal.enqueue_session(quickcep_session_id="qs-nofirst", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    cal.save_draft(quickcep_session_id="qs-nofirst", draft_html="<p>hello</p>", env="LIVE")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"draft_ready": "ai-draft", "processing": "ai-proc"},
        "business": {"awaiting_customer": "biz-wait"},
        "inquiry_by_category": {},
    }), patch.object(sh, "_run_quickcep_cli", return_value={"ok": True}):
        result = sh.apply_handoff(
            quickcep_session_id="qs-nofirst",
            phase="draft_ready",
            env="LIVE",
            chat_session_id="chat-1",
            skip_quickcep=True,
        )
    assert result["ok"] is True
    ev = _latest_event(cal, session_row_id=r["session"]["id"], event_type="session_handoff")
    payload = json.loads(ev["payload_json"])
    assert payload["via_resume"] is False
    assert payload["escalation_id"] is None


def test_via_resume_true_when_resuming_escalation_exists(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    sh = _load("session_handoff")

    r = cal.enqueue_session(quickcep_session_id="qs-resume", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="awaiting_expert")
    eid = cal.open_escalation(quickcep_session_id="qs-resume", reason="test", env="LIVE")
    # Claim → resuming.
    claimed = cal.claim_escalation_reply(
        escalation_id=eid, operator_answer="expert answer",
        decided_by="op1", feishu_reply_message_id="rm-1",
    )
    assert claimed is True
    cal.save_draft(quickcep_session_id="qs-resume", draft_html="<p>resume draft</p>", env="LIVE")

    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"draft_ready": "ai-draft", "processing": "ai-proc"},
        "business": {"awaiting_customer": "biz-wait"},
        "inquiry_by_category": {},
    }), patch.object(sh, "_run_quickcep_cli", return_value={"ok": True}):
        result = sh.apply_handoff(
            quickcep_session_id="qs-resume",
            phase="draft_ready",
            env="LIVE",
            chat_session_id="chat-1",
            skip_quickcep=True,
        )
    assert result["ok"] is True
    ev = _latest_event(cal, session_row_id=r["session"]["id"], event_type="session_handoff")
    payload = json.loads(ev["payload_json"])
    assert payload["via_resume"] is True
    assert payload["escalation_id"] == eid
