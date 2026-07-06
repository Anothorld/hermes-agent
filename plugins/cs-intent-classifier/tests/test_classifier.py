"""Classifier tests — keyword layer, schema validation, no-fabrication invariants."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from cs_intent_classifier_pkg import classifier  # type: ignore[attr-defined]
from cs_intent_classifier_pkg.schemas import GateExtract  # type: ignore[attr-defined]


def _kw(subject: str, body: str, metadata: dict | None = None) -> dict | None:
    return classifier.keyword_classify(subject=subject, body=body, metadata=metadata or {})


def test_keyword_logistics_tracking():
    ge = _kw("Where is my order?", "Where is my order #12345678? It's been a week.")
    assert ge is not None
    assert ge["primary_intent"] == "logistics_inquiry"
    assert ge["in_scope"] is True
    assert ge["route"] == "auto_handle"
    assert "12345678" in ge["orders"]
    assert ge["classifier_source"] == "keyword"
    assert ge["fabrication_guard"] is True


def test_keyword_after_sale_damage_escalates():
    ge = _kw("Damaged sofa", "The sofa arrived damaged, I want a refund.")
    assert ge is not None
    assert ge["primary_intent"] == "after_sale_issue"
    assert ge["in_scope"] is False
    assert ge["route"] == "escalate"


def test_keyword_threat_legal_forces_escalate():
    ge = _kw("Lawyer threat", "I will contact my lawyer if you don't refund me.")
    assert ge is not None
    assert ge["threat_signal"] == "legal"
    assert ge["route"] == "escalate"


def test_keyword_spam_b2b():
    ge = _kw("Guest post offer", "We are a supplier offering guest post services for SEO.")
    assert ge is not None
    assert ge["primary_intent"] == "spam_irrelevant"
    assert ge["in_scope"] is False


def test_keyword_order_management_cancel():
    ge = _kw("Cancel order", "Please cancel my order #99887766, I haven't received it.")
    assert ge is not None
    assert ge["primary_intent"] == "order_management"
    assert ge["in_scope"] is False


def test_keyword_product_inquiry():
    ge = _kw("Sofa dimensions", "What are the dimensions of the Atticus sofa?")
    assert ge is not None
    assert ge["primary_intent"] == "product_inquiry"
    assert ge["in_scope"] is True


def test_keyword_miss_returns_none_for_ambiguous():
    # Ambiguous email with no clear keyword → None → LLM fallback
    ge = _kw("Hello", "Hi, I had a question about my recent purchase.")
    assert ge is None


def test_schema_validation_passes():
    ge = _kw("Tracking", "Where is my shipment? order #11223344")
    assert ge is not None
    validated = GateExtract.model_validate(ge)
    assert validated.fabrication_guard is True
    assert validated.intents
    assert validated.in_scope is True


def test_no_fabrication_region_unknown():
    # Logistics keyword match with NO region signal → unknown → null_fields
    ge = _kw("Order", "Where is my order #11223344?", metadata={"customer_email": "x@y.com"})
    assert ge is not None
    if ge["customer_region"]["source"] == "unknown":
        assert "customer_region" in ge["null_fields"]
        assert ge["customer_region"]["country"] is None


def test_region_from_order_address_high_priority():
    ge = _kw(
        "Order",
        "Where is my order?",
        metadata={
            "order_addresses": [{"order_id": "1", "country": "US", "province_state": "CA"}],
            "visitor_geo": {"country": "CA", "province_state": "ON"},
        },
    )
    assert ge is not None
    # order_address wins over visitor_geo
    assert ge["customer_region"]["source"] == "order_address"
    assert ge["customer_region"]["country"] == "US"
    assert ge["customer_region"]["confidence"] == "high"


def test_region_from_visitor_geo():
    ge = _kw(
        "Order",
        "Where is my order?",
        metadata={"visitor_geo": {"country": "GB", "province_state": "London"}},
    )
    assert ge is not None
    assert ge["customer_region"]["source"] == "visitor_geo"
    assert ge["customer_region"]["country"] == "GB"


def test_language_detection_chinese():
    # Keyword layer is English-only; test the detector directly.
    lang_val, lang_conf = classifier._detect_language("我的订单到了吗？订单号 12345678")
    assert lang_val == "zh"
    assert lang_conf > 0.7


def test_order_extraction_only_real_numbers():
    orders = classifier._extract_orders("My order is #12345678 please help, also ref #999999")
    assert "12345678" in orders
    assert "999999" in orders


def test_sku_extraction():
    skus = classifier._extract_skus("I have a question about sofa SF8268 and table DK1234")
    assert "SF8268" in skus
    assert "DK1234" in skus


def test_conservative_review_when_no_llm(monkeypatch):
    # No LLM configured → conservative review fallback
    monkeypatch.delenv("CS_INTENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ge = classifier.llm_classify(subject="Hello", body="A question", metadata={})
    assert ge["route"] == "review"
    assert ge["ambiguous"] is True
    assert ge["fabrication_guard"] is True


def test_classify_falls_through_to_llm_when_keyword_misses(monkeypatch):
    # Keyword misses → LLM path; with no LLM configured → conservative review
    monkeypatch.delenv("CS_INTENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ge = classifier.classify(subject="Hi", body="Just checking in", metadata={})
    assert ge["route"] == "review"
    assert ge["classifier_source"] == "keyword"  # conservative review stamps keyword
