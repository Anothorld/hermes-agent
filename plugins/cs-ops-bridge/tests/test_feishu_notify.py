"""Tests for deterministic Feishu escalation notify."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_feishu_test"


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


def test_open_escalation_auto_sends_feishu(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    notify = _load("feishu_notify")
    fc = _load("feishu_client")

    cal.enqueue_session(quickcep_session_id="qs-feishu", message_id="m1", env="LIVE")

    fake = fc.FeishuSendResult(
        ok=True,
        message_id="om_test_msg",
        thread_id="om_test_msg",
        chat_id="oc_chat",
    )
    with patch.object(notify, "notify_escalation_opened", return_value=fake) as mocked:
        eid = cal.open_escalation(
            quickcep_session_id="qs-feishu",
            reason="product specs unknown",
            env="LIVE",
        )
        assert eid
        send = notify.notify_escalation_opened(
            escalation_id=eid,
            quickcep_session_id="qs-feishu",
            reason="product specs unknown",
        )
        cal.update_escalation_feishu(
            escalation_id=eid,
            feishu_chat_id=send.chat_id,
            feishu_thread_id=send.thread_id,
            feishu_message_id=send.message_id,
        )
        mocked.assert_called_once()
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["feishu_message_id"] == "om_test_msg"
    assert esc["feishu_thread_id"] == "om_test_msg"


def test_build_escalation_text_includes_esc_id(monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    notify = _load("feishu_notify")
    text = notify.build_escalation_text(
        escalation_id=42,
        customer_email="tshea2121@gmail.com",
        reason="need assembly video",
        urgency="low",
        question_to_operator="Do we have SF8181 assembly guide?",
        email_summary="客户需要 SF8181 安装说明。",
        email_quote="Please send the SF8181 assembly instructions.",
    )
    assert "[ESC:42]" in text
    assert "tshea2121@gmail.com" in text
    assert "客户需要 SF8181" in text
    assert "assembly instructions" in text
    assert "请务必先上传附件" in text
    assert "/escalations/42/upload" in text


def test_reply_to_message_includes_msg_type(monkeypatch):
    fc = _load("feishu_client")
    captured: dict = {}

    class FakeResp:
        def read(self):
            return b'{"code":0,"data":{"message_id":"om_reply"}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=20):
        import json as _json

        captured["body"] = _json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with patch.object(fc, "tenant_access_token", return_value="tok"):
        result = fc.reply_to_message(message_id="om_root", text="hello")
    assert result.ok
    assert captured["body"]["msg_type"] == "text"
    assert "hello" in captured["body"]["content"]


def test_custom_escalation_message_appends_vault_link(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    notify = _load("feishu_notify")
    with patch.object(
        notify,
        "send_group_text",
        return_value=notify.FeishuSendResult(ok=True, message_id="m1"),
    ) as mock_send:
        result = notify.notify_escalation_opened(
            escalation_id=7,
            quickcep_session_id="qs-1",
            reason="custom",
            escalation_message="Custom ESC body only",
            auto_send_feishu=True,
        )
    assert result.ok
    sent_text = mock_send.call_args.kwargs.get("text") or mock_send.call_args[0][1]
    assert "Custom ESC body only" in sent_text
    assert "/escalations/7/upload" in sent_text
    assert "请务必先上传附件" in sent_text
