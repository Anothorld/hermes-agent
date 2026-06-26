"""Tests for deterministic order section in Feishu escalations."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_escalation_orders_test"


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


def test_extract_order_ids_skips_quickcep_session_id():
    orders_mod = _load("escalation_orders")
    session_id = "2547790329987325954"
    ids = orders_mod.extract_order_ids_from_text(
        f"QuickCEP session {session_id} for reference",
        "order 260503381430263568 replacement",
        exclude_ids=[session_id],
    )
    assert session_id not in ids
    assert "260503381430263568" in ids


def test_is_probable_numeric_order_id():
    orders_mod = _load("escalation_orders")
    assert orders_mod.is_probable_quickcep_session_id("2547790329987325954") is True
    assert orders_mod.is_probable_numeric_order_id("2547790329987325954") is False
    assert orders_mod.is_probable_numeric_order_id("260503381430263568") is True


def test_extract_order_ids_from_escalation_text():
    orders_mod = _load("escalation_orders")
    ids = orders_mod.extract_order_ids_from_text(
        "替换单WEC0202606130019的白手套服务",
        "order 260503381430263568 replacement",
    )
    assert "WEC0202606130019" in ids
    assert "260503381430263568" in ids


def test_format_order_section_tracking_only_from_hints():
    orders_mod = _load("escalation_orders")
    text = orders_mod.format_order_section(
        {
            "orders": [],
            "tracking": {
                "summaries": [
                    {
                        "orderId": "260503381430263568",
                        "found": True,
                        "status": "delivered",
                        "trackingNumber": "700447978851",
                    }
                ]
            },
        }
    )
    assert "260503381430263568" in text
    assert "运单:700447978851" in text


def test_format_order_section_with_tracking():
    orders_mod = _load("escalation_orders")
    text = orders_mod.format_order_section(
        {
            "orders": [
                {
                    "orderId": "260503381430263568",
                    "totalPrice": "1299.00",
                    "currency": "USD",
                    "financialStatus": "paid",
                    "fulfillmentStatus": "partial",
                    "lineItems": [{"title": "Aurora Power Sofa Bed"}],
                }
            ],
            "tracking": {
                "summaries": [
                    {
                        "orderId": "260503381430263568",
                        "found": True,
                        "status": "transit",
                        "trackingNumber": "1Z999",
                        "earliestEdd": "2026-06-29",
                        "latestEdd": "2026-07-02",
                    }
                ]
            },
        }
    )
    assert "260503381430263568" in text
    assert "Aurora Power Sofa Bed" in text
    assert "运单:1Z999" in text


def test_format_order_section_empty():
    orders_mod = _load("escalation_orders")
    assert "未查到关联订单" in orders_mod.format_order_section({"orders": []})


def test_build_escalation_text_includes_order_section():
    notify = _load("feishu_notify")
    text = notify.build_escalation_text(
        escalation_id=23,
        customer_email="jefferylee.h@gmail.com",
        reason="WGA logistics",
        order_section="· 260503381430263568 支付:paid / 履约:partial",
        email_summary="客户咨询白手套服务。",
        email_quote="Please confirm white glove delivery.",
    )
    assert "📦 订单信息:" in text
    assert "260503381430263568" in text
    assert text.index("📦 订单信息:") < text.index("📩 客户来信摘要:")


def test_notify_escalation_fetches_orders(monkeypatch):
    notify = _load("feishu_notify")
    fc = _load("feishu_client")
    with patch.object(
        notify,
        "fetch_escalation_order_context",
        return_value={
            "orders": [{"orderId": "260619360220455021", "financialStatus": "paid", "fulfillmentStatus": "fulfilled"}],
            "tracking": {"summaries": []},
        },
    ):
        with patch.object(
            notify,
            "send_group_text",
            return_value=fc.FeishuSendResult(ok=True, message_id="m1", thread_id="m1", chat_id="c1"),
        ) as send:
            notify.notify_escalation_opened(
                escalation_id=99,
                quickcep_session_id="2547790329987325954",
                reason="logistics",
                customer_email="jefferylee.h@gmail.com",
                email_summary="客户咨询物流。",
                email_quote="Where is my replacement order?",
                env="LIVE",
            )
    sent_text = send.call_args.kwargs["text"]
    assert "📦 订单信息:" in sent_text
    assert "260619360220455021" in sent_text


def test_custom_message_skips_auto_order_block():
    notify = _load("feishu_notify")
    fc = _load("feishu_client")
    with patch.object(notify, "fetch_escalation_order_context") as fetch_ctx:
        with patch.object(
            notify,
            "send_group_text",
            return_value=fc.FeishuSendResult(ok=True, message_id="m1", thread_id="m1", chat_id="c1"),
        ) as send:
            notify.notify_escalation_opened(
                escalation_id=100,
                quickcep_session_id="2547790329987325954",
                reason="ignored",
                escalation_message="[ESC:100] custom body without order block",
                env="LIVE",
            )
    fetch_ctx.assert_not_called()
    sent_text = send.call_args.kwargs["text"]
    assert sent_text == "[ESC:100] custom body without order block"
    assert "📦 订单信息:" not in sent_text
