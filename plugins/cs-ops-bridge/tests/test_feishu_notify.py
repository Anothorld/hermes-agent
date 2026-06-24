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


def test_build_escalation_text_includes_esc_id():
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
