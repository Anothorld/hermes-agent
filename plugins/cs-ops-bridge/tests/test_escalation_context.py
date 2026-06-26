"""Tests for Feishu escalation notify (agent-provided email + summary/quote)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_escalation_ctx_test"


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


def test_validate_requires_chinese_summary_and_original_quote():
    notify = _load("feishu_notify")
    with pytest.raises(ValueError, match="email_summary required"):
        notify.validate_feishu_notify_inputs(
            auto_send_feishu=True,
            escalation_message=None,
            customer_email="a@b.com",
            email_summary="",
            email_quote="Where is my order?",
            quickcep_session_id="s1",
            env="LIVE",
        )
    with pytest.raises(ValueError, match="email_quote required"):
        notify.validate_feishu_notify_inputs(
            auto_send_feishu=True,
            escalation_message=None,
            customer_email="a@b.com",
            email_summary="客户咨询物流进度",
            email_quote="",
            quickcep_session_id="s1",
            env="LIVE",
        )


def test_build_escalation_text_separates_summary_and_quote():
    notify = _load("feishu_notify")
    text = notify.build_escalation_text(
        escalation_id=42,
        customer_email="tessa@example.com",
        reason="need assembly video",
        urgency="low",
        question_to_operator="Do we have SF8181 assembly guide?",
        email_summary="客户需要 SF8181 的安装视频及缺件清单。",
        email_quote="I still need the assembly video for model SF8181.",
    )
    assert "客户邮箱: tessa@example.com" in text
    assert "客户来信摘要:" in text
    assert "客户需要 SF8181 的安装视频及缺件清单。" in text
    assert "原始来信：" in text
    assert "I still need the assembly video for model SF8181." in text
    assert text.index("客户需要") < text.index("I still need")


def test_notify_escalation_uses_agent_summary_and_quote():
    notify = _load("feishu_notify")
    fc = _load("feishu_client")

    with patch.object(
        notify,
        "send_group_text",
        return_value=fc.FeishuSendResult(ok=True, message_id="m1", thread_id="m1", chat_id="c1"),
    ) as send:
        result = notify.notify_escalation_opened(
            escalation_id=7,
            quickcep_session_id="2544719278035312643",
            reason="logistics delay",
            question_to_operator="Can we share latest tracking?",
            customer_email="james@example.com",
            email_summary="客户询问订单 POV-12345 物流进度，称已超过预计送达时间。",
            email_quote="Where is my order POV-12345? It was due last week.",
            env="LIVE",
        )
    assert result.ok
    sent_text = send.call_args.kwargs["text"]
    assert "客户询问订单 POV-12345" in sent_text
    assert "Where is my order POV-12345?" in sent_text
