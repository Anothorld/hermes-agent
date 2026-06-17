"""Tests for session_handoff lifecycle tagging and notes."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_handoff_test"


def _load_pkg_module(sub: str):
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


def test_compose_draft_ready_removes_escalation_tag():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"draft_ready": "ai-draft", "processing": "ai-proc", "closed": "ai-closed"},
        "business": {"awaiting_customer": "biz-wait", "escalation": "biz-esc"},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("draft_ready", {"customer_need": "Track order"})
    assert "ai-draft" in plan.tags_add
    assert "biz-wait" in plan.tags_add
    assert "biz-esc" in plan.tags_remove
    assert "ai-proc" in plan.tags_remove
    assert "[AI-CS]" in plan.note_body
    assert "Track order" in plan.note_body


def test_compose_operator_sent():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"closed": "ai-closed", "draft_ready": "ai-draft"},
        "business": {"awaiting_customer": "biz-wait"},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("operator_sent", {
            "operator_id": "op-1",
            "email_subject": "Re: Order",
            "operator_hint": "已回复物流问题",
        })
    assert "ai-closed" in plan.tags_add
    assert "ai-draft" in plan.tags_remove
    assert "biz-wait" in plan.tags_add
    assert plan.target_status == "operator_replied"
    assert "操作员已发送回复" in plan.note_body


def test_apply_handoff_writes_cal_events(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    sh = _load_pkg_module("session_handoff")
    cal.enqueue_session(quickcep_session_id="sess-1", message_id="m1", env="LIVE", chat_session_id="chat-1")
    cal.update_session_status(session_row_id=1, status="draft_ready")

    with patch.object(sh, "apply_quickcep_tags", return_value=[]), patch.object(
        sh, "apply_quickcep_note", return_value={"ok": True}
    ):
        result = sh.apply_handoff(
            quickcep_session_id="sess-1",
            phase="draft_ready",
            env="LIVE",
            context={"customer_need": "Need help"},
            chat_session_id="chat-1",
            skip_quickcep=False,
        )
    assert result["ok"] is True
    ctx = cal.get_dispatch_context(quickcep_session_id="sess-1", env="LIVE")
    assert ctx["session"]["status"] == "draft_ready"
    types = [e["event_type"] for e in ctx["recent_events"]]
    assert "session_handoff" in types


def test_handle_operator_send_dedup(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    sh = _load_pkg_module("session_handoff")
    cal.enqueue_session(quickcep_session_id="sess-2", message_id="m1", env="LIVE", chat_session_id="chat-2")
    cal.update_session_status(session_row_id=1, status="draft_ready")
    cal.write_facts(
        quickcep_session_id="sess-2",
        namespaces={"handoff": {"last_operator_outbound_id": "msg-99"}},
        env="LIVE",
    )

    with patch.object(sh, "apply_handoff") as mock_apply:
        out = sh.handle_operator_send(
            {"chatSubSessionId": "sess-2", "id": "msg-99", "channel": "email"},
            env="LIVE",
        )
    assert out.get("skipped") is True
    mock_apply.assert_not_called()


def test_enqueue_resets_operator_replied(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    r1 = cal.enqueue_session(quickcep_session_id="sess-3", message_id="m1", env="LIVE")
    sid = r1["session"]["id"]
    cal.update_session_status(session_row_id=sid, status="operator_replied")
    r2 = cal.enqueue_session(quickcep_session_id="sess-3", message_id="m2", env="LIVE")
    assert r2["session"]["status"] == "pending"
    assert r2["should_launch"] is True


def test_stale_handoff_skips_after_operator_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    sh = _load_pkg_module("session_handoff")
    r1 = cal.enqueue_session(quickcep_session_id="sess-4", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="operator_replied")

    out = sh.apply_handoff(
        quickcep_session_id="sess-4",
        phase="draft_ready",
        env="LIVE",
        context={"customer_need": "late agent"},
        skip_quickcep=True,
    )
    assert out.get("skipped") is True
    assert cal.get_session(quickcep_session_id="sess-4", env="LIVE")["status"] == "operator_replied"


def test_untracked_operator_send_skipped_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    sh = _load_pkg_module("session_handoff")
    out = sh.handle_operator_send(
        {"chatSubSessionId": "unknown", "id": "m1", "channel": "email"},
        env="LIVE",
    )
    assert out.get("skipped") is True
    assert "CAL" in (out.get("reason") or "")


def test_compose_note_masks_email():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"processing": "ai-proc"},
        "business": {},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("processing", {
            "customer_need": "Contact me at secret@example.com",
        })
    assert "secret@example.com" not in plan.note_body
    assert "@" in plan.note_body
