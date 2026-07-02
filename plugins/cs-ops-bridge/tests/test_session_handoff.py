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
        plan = sh.compose_handoff("draft_ready", {"customer_need": "客户咨询物流进度"})
    assert "ai-draft" in plan.tags_add
    assert "biz-wait" in plan.tags_add
    assert "biz-esc" in plan.tags_remove
    assert "ai-proc" in plan.tags_remove
    assert "[智能客服]" in plan.note_body
    assert "客户咨询物流进度" in plan.note_body


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


def test_normalize_handoff_phase_aliases():
    sh = _load_pkg_module("session_handoff")
    assert sh.normalize_handoff_phase("processed_by_human") == "reviewed"
    assert sh.normalize_handoff_phase("completed") == "reviewed"
    assert sh.normalize_handoff_phase("draft_ready") == "draft_ready"


def test_compose_handoff_accepts_phase_aliases():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"closed": "ai-closed", "processing": "ai-proc", "draft_ready": "ai-draft"},
        "business": {},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("processed_by_human", {"customer_need": "已由人工处理"})
    assert "ai-closed" in plan.tags_add
    assert plan.target_status == "reviewed"


def test_compose_unknown_phase_raises():
    sh = _load_pkg_module("session_handoff")
    with pytest.raises(ValueError, match="unknown handoff phase"):
        sh.compose_handoff("not_a_real_phase", {})


def test_apply_handoff_writes_cal_events(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.delenv("CS_OPS_DRAFT_SAVE_LEGACY_QUICKCEP", raising=False)
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    sh = _load_pkg_module("session_handoff")
    cal.enqueue_session(quickcep_session_id="sess-1", message_id="m1", env="LIVE", chat_session_id="chat-1")
    cal.update_session_status(session_row_id=1, status="draft_ready")
    # §4.13 B guard: draft_ready handoff requires a CAL draft.
    cal.save_draft(quickcep_session_id="sess-1", draft_html="<p>draft</p>", source="agent", env="LIVE")

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


def test_followup_while_busy_skips_quickcep_note(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    sh = _load_pkg_module("session_handoff")
    r1 = cal.enqueue_session(quickcep_session_id="sess-busy", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="awaiting_expert")

    with patch.object(sh, "apply_quickcep_tags") as tags, patch.object(sh, "apply_quickcep_note") as note:
        result = sh.apply_handoff(
            quickcep_session_id="sess-busy",
            phase="followup_while_busy",
            env="LIVE",
            context={"customer_need": "客户追加消息"},
            chat_session_id="c1",
        )

    assert result["ok"] is True
    tags.assert_not_called()
    note.assert_not_called()


def test_duplicate_awaiting_expert_skips_second_note(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    sh = _load_pkg_module("session_handoff")
    r1 = cal.enqueue_session(quickcep_session_id="sess-expert", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")

    with patch.object(sh, "apply_quickcep_tags", return_value=[]), patch.object(
        sh, "apply_quickcep_note", return_value={"ok": True}
    ) as note:
        sh.apply_handoff(
            quickcep_session_id="sess-expert",
            phase="awaiting_expert",
            env="LIVE",
            context={"customer_need": "first"},
            chat_session_id="c1",
        )
        sh.apply_handoff(
            quickcep_session_id="sess-expert",
            phase="awaiting_expert",
            env="LIVE",
            context={"customer_need": "duplicate"},
            chat_session_id="c1",
        )

    assert note.call_count == 1
    assert cal.get_session(quickcep_session_id="sess-expert", env="LIVE")["status"] == "awaiting_expert"


def test_awaiting_expert_after_draft_ready_still_syncs_quickcep(monkeypatch, tmp_path):
    """Escalation after draft-save must replace AI-草稿待审 with AI-待专家 in QuickCEP."""
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    sh = _load_pkg_module("session_handoff")
    cal_mod = _load_pkg_module("cal")

    r1 = cal.enqueue_session(quickcep_session_id="sess-draft-then-esc", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="draft_ready")

    cal_mod.open_escalation(quickcep_session_id="sess-draft-then-esc", reason="need expert", env="LIVE")

    with patch.object(sh, "apply_quickcep_tags", return_value=[{"ok": True}]) as tags, patch.object(
        sh, "apply_quickcep_note", return_value={"ok": True}
    ) as note:
        out = sh.apply_handoff(
            quickcep_session_id="sess-draft-then-esc",
            phase="awaiting_expert",
            env="LIVE",
            context={"customer_need": "认证信息缺失", "feishu_thread_id": "om_test"},
            chat_session_id="c1",
        )

    assert out.get("skipped") is not True
    tags.assert_called_once()
    note.assert_called_once()
    assert cal.get_session(quickcep_session_id="sess-draft-then-esc", env="LIVE")["status"] == "awaiting_expert"


def test_processing_handoff_on_awaiting_expert_skips_note(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    sh = _load_pkg_module("session_handoff")
    r1 = cal.enqueue_session(quickcep_session_id="sess-regress", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="awaiting_expert")

    with patch.object(sh, "apply_quickcep_tags") as tags, patch.object(sh, "apply_quickcep_note") as note:
        sh.apply_handoff(
            quickcep_session_id="sess-regress",
            phase="processing",
            env="LIVE",
            context={"customer_need": "late processing"},
            chat_session_id="c1",
        )

    tags.assert_not_called()
    note.assert_not_called()
    assert cal.get_session(quickcep_session_id="sess-regress", env="LIVE")["status"] == "awaiting_expert"


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


def test_compose_processing_defaults_chinese():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"processing": "ai-proc"},
        "business": {},
        "inquiry_by_category": {"logistics": "inq-log"},
    }):
        plan = sh.compose_handoff("processing", {
            "classify": {"route": "auto_handle", "category": "logistics"},
        })
    assert "自动处理" in plan.note_body
    assert "物流咨询" in plan.note_body
    assert "inbound" not in plan.note_body.lower()
    assert "[智能客服]" in plan.note_body
    assert "协调世界时" in plan.note_body


def test_localize_agent_english_phrases():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"draft_ready": "ai-draft"},
        "business": {"awaiting_customer": "biz-wait"},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("draft_ready", {
            "customer_need": "客户要查物流",
            "actions_taken": "draft-save",
            "operator_hint": "Console relaunch if needed",
        })
    assert "草稿已生成" in plan.note_body
    assert "工单列表" in plan.note_body
    assert "draft-save" not in plan.note_body.lower()
    assert "Console" not in plan.note_body


def test_failed_sanitizes_technical_agent_note():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"failed": "ai-fail"},
        "business": {},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("failed", {
            "actions_taken": (
                "处理失败：草稿保存失败：仅邮件域名被解析为命令。"
                "使用 --content-file 并加入后续跟进"
            ),
            "error": "draft-save shell mangled",
        })
    assert "--content-file" not in plan.note_body
    assert "被解析为命令" not in plan.note_body
    assert "网关" not in plan.note_body
    assert "桥接服务" not in plan.note_body
    assert "处理失败" in plan.note_body
    assert "草稿" in plan.note_body


def test_failed_default_operator_hint_is_business_facing():
    sh = _load_pkg_module("session_handoff")
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"failed": "ai-fail"},
        "business": {},
        "inquiry_by_category": {},
    }):
        plan = sh.compose_handoff("failed", {"error": "gateway launch failed"})
    assert "网关" not in plan.note_body
    assert "桥接服务" not in plan.note_body
    assert "日志" not in plan.note_body
    assert "人工" in plan.note_body


# ── §4.13 B bridge guard: draft_ready requires a CAL draft ──────────────

def test_draft_ready_blocked_without_cal_draft(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.delenv("CS_OPS_DRAFT_SAVE_LEGACY_QUICKCEP", raising=False)
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    sh = _load_pkg_module("session_handoff")
    r = cal.enqueue_session(quickcep_session_id="sess-guard", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")

    out = sh.apply_handoff(
        quickcep_session_id="sess-guard",
        phase="draft_ready",
        env="LIVE",
        context={"customer_need": "need"},
        skip_quickcep=True,
    )
    assert out["ok"] is False
    assert out["error"] == "draft_ready_requires_cal_draft"
    # status must NOT have advanced to draft_ready
    assert cal.get_session(quickcep_session_id="sess-guard", env="LIVE")["status"] == "processing"


def test_draft_ready_allowed_with_cal_draft(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.delenv("CS_OPS_DRAFT_SAVE_LEGACY_QUICKCEP", raising=False)
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    sh = _load_pkg_module("session_handoff")
    r = cal.enqueue_session(quickcep_session_id="sess-guard-ok", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    cal.save_draft(quickcep_session_id="sess-guard-ok", draft_html="<p>hi</p>", source="agent", env="LIVE")

    with patch.object(sh, "apply_quickcep_tags", return_value=[]), patch.object(sh, "apply_quickcep_note", return_value={"ok": True}):
        out = sh.apply_handoff(
            quickcep_session_id="sess-guard-ok",
            phase="draft_ready",
            env="LIVE",
            context={"customer_need": "need"},
            skip_quickcep=True,
        )
    assert out["ok"] is True
    assert cal.get_session(quickcep_session_id="sess-guard-ok", env="LIVE")["status"] == "draft_ready"


def test_draft_ready_guard_bypassed_in_legacy_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_DRAFT_SAVE_LEGACY_QUICKCEP", "1")  # M3: drafts in QuickCEP, not CAL
    cal = _load_pkg_module("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    sh = _load_pkg_module("session_handoff")
    r = cal.enqueue_session(quickcep_session_id="sess-legacy", message_id="m1", env="LIVE", chat_session_id="c1")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    # no CAL draft — but legacy mode allows it

    with patch.object(sh, "apply_quickcep_tags", return_value=[]), patch.object(sh, "apply_quickcep_note", return_value={"ok": True}):
        out = sh.apply_handoff(
            quickcep_session_id="sess-legacy",
            phase="draft_ready",
            env="LIVE",
            context={"customer_need": "need"},
            skip_quickcep=True,
        )
    assert out["ok"] is True
    assert cal.get_session(quickcep_session_id="sess-legacy", env="LIVE")["status"] == "draft_ready"
