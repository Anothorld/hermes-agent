"""PR3 tests: permanent inbound_skipped events, force_status busy guard, ad-skip no-override-busy."""

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
_PKG = "cs_ops_bridge_pr3_skip_test"


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


# ── force_status default behavior unchanged ──────────────────────────

def test_force_status_none_preserves_existing_behavior(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    res = cal.enqueue_session(quickcep_session_id="qs-normal", message_id="m1", env="LIVE")
    assert res["should_launch"] is True
    assert res["session"]["status"] == "pending"
    ev = _latest_event(cal, session_row_id=res["session"]["id"], event_type="inbound_received")
    assert ev is not None


# ── permanent skip: force_status=skipped on new session ──────────────

def test_force_status_skipped_new_session(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    res = cal.enqueue_session(
        quickcep_session_id="qs-skip", message_id="m1", env="LIVE",
        force_status="skipped",
        skip_event_payload={"gate": "blocklist", "sender": "x@povison-inc.com"},
    )
    assert res["session"]["status"] == "skipped"
    assert res["should_launch"] is False
    ev = _latest_event(cal, session_row_id=res["session"]["id"], event_type="inbound_skipped")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "blocklist"
    # PR3: enqueue_session event payloads are sanitized (mask_string) — the
    # sender email is masked to first-char + ***@domain.
    assert payload["sender"] == "x***@povison-inc.com"
    assert payload["status"] == "skipped"


# ── busy guard: force_status must NOT override processing ────────────

def test_force_status_does_not_override_busy(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r1 = cal.enqueue_session(quickcep_session_id="qs-busy", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")
    # A non-email / ad follow-up arrives on the busy session.
    res = cal.enqueue_session(
        quickcep_session_id="qs-busy", message_id="m2", env="LIVE",
        force_status="skipped",
        skip_event_payload={"gate": "ad"},
    )
    # Status must remain processing (busy guard).
    assert res["session"]["status"] == "processing"
    assert res["should_launch"] is False
    # But the inbound_skipped audit event is still written.
    ev = _latest_event(cal, session_row_id=r1["session"]["id"], event_type="inbound_skipped")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "ad"
    assert payload["status"] == "processing"  # not overridden


# ── _launch_for_message: non_email gate enqueues ─────────────────────

def test_non_email_gate_enqueues_skip(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    watcher._launch_for_message({
        "chatSubSessionId": "qs-nonemail",
        "id": "m1",
        "channel": "chat",  # non-email
    })
    sess = cal.get_session(quickcep_session_id="qs-nonemail", env="LIVE")
    assert sess is not None
    assert sess["status"] == "skipped"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "non_email"


# ── _launch_for_message: blocklist gate enqueues ─────────────────────

def test_blocklist_gate_enqueues_skip(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    watcher._launch_for_message({
        "chatSubSessionId": "qs-block",
        "id": "m1",
        "channel": "email",
        "email": "logistics@povison-inc.com",
        "intentionTags": ["产品咨询"],
    })
    sess = cal.get_session(quickcep_session_id="qs-block", env="LIVE")
    assert sess["status"] == "skipped"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "blocklist"


# ── _launch_for_message: intent_gate not_allowed enqueues ────────────

def test_intent_not_allowed_enqueues_skip(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    with patch.object(watcher, "check_intent_gate") as gate_fn:
        gate_fn.return_value = watcher.check_intent_gate.__class__.__new__(
            watcher.check_intent_gate.__class__
        ) if False else types.SimpleNamespace(
            allowed=False, reason="intention_not_allowed (allowed: ...)", tags=("其它",)
        )
        watcher._launch_for_message({
            "chatSubSessionId": "qs-intent-no",
            "id": "m1",
            "channel": "email",
            "email": "cust@example.com",
            "intentionTags": ["其它"],
        })
    sess = cal.get_session(quickcep_session_id="qs-intent-no", env="LIVE")
    assert sess["status"] == "skipped"
    ev = _latest_event(cal, session_row_id=sess["id"], event_type="inbound_skipped")
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "intent_gate"


# ── _launch_for_message: no_intention_tags stays log-only (no CAL row) ──

def test_no_intention_tags_stays_log_only(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    with patch.object(watcher, "check_intent_gate") as gate_fn:
        gate_fn.return_value = types.SimpleNamespace(
            allowed=False, reason="no_intention_tags", tags=()
        )
        watcher._launch_for_message({
            "chatSubSessionId": "qs-notags",
            "id": "m1",
            "channel": "email",
            "email": "cust@example.com",
        })
    # No CAL row — retry mechanism preserved.
    assert cal.get_session(quickcep_session_id="qs-notags", env="LIVE") is None


# ── ad skip no longer overrides busy status ──────────────────────────

def test_ad_skip_does_not_override_processing(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    # First message: normal launch.
    r1 = cal.enqueue_session(quickcep_session_id="qs-adbusy", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")
    # Second message: ad follow-up arrives while processing.
    with patch.object(watcher, "detect_ad_from_info", return_value=True), \
         patch.object(watcher, "AD_TAG_ID", "ad-tag-1"), \
         patch("cs_ops_bridge_pr3_skip_test.session_handoff._run_quickcep_cli", return_value={"ok": True}):
        # Reimport session_handoff inside watcher uses relative import; patch via watcher.
        watcher._launch_for_message({
            "chatSubSessionId": "qs-adbusy",
            "id": "m2",
            "channel": "email",
            "email": "spam@example.com",
            "email_subject": "buy our services",
        })
    sess = cal.get_session(quickcep_session_id="qs-adbusy", env="LIVE")
    # Status must remain processing — the ad skip must NOT override.
    assert sess["status"] == "processing"
    # But inbound_skipped (gate=ad) and ad_email_detected events are written.
    ev = _latest_event(cal, session_row_id=r1["session"]["id"], event_type="inbound_skipped")
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["gate"] == "ad"


# ── ad skip on idle session marks skipped ────────────────────────────

def test_ad_skip_on_idle_marks_skipped(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    watcher = _load("quickcep_watcher")
    with patch.object(watcher, "detect_ad_from_info", return_value=True), \
         patch.object(watcher, "AD_TAG_ID", "ad-tag-1"):
        watcher._launch_for_message({
            "chatSubSessionId": "qs-adidle",
            "id": "m1",
            "channel": "email",
            "email": "spam@example.com",
            "email_subject": "buy our services",
        })
    sess = cal.get_session(quickcep_session_id="qs-adidle", env="LIVE")
    assert sess["status"] == "skipped"
