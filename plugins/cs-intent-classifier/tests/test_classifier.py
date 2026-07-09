"""Classifier tests — keyword layer, schema validation, no-fabrication invariants."""

from __future__ import annotations

import json
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


def test_keyword_order_management_afterpay_not_approved():
    ge = _kw(
        "Payment issue",
        "The after pay wasn't approved that is why i didn't finish the sale",
    )
    assert ge is not None
    assert ge["primary_intent"] == "order_management"
    assert ge["in_scope"] is False
    assert ge["classifier_source"] == "keyword"
    assert "checkout_payment" in ge["summary_zh"] or "支付" in ge["summary_zh"]


def test_keyword_order_management_afterpay_declined_question():
    ge = _kw("Afterpay", "My Afterpay was declined, can I pay another way?")
    assert ge is not None
    assert ge["primary_intent"] == "order_management"
    assert ge["in_scope"] is False


def test_keyword_afterpay_not_classified_as_after_sale():
    ge = _kw(
        "Re: Order",
        "The after pay wasn't approved that is why i didn't finish the sale",
    )
    assert ge is not None
    assert ge["primary_intent"] != "after_sale_issue"
    assert ge["primary_intent"] == "order_management"


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


def test_augment_prompt_with_learning_appends_blocks(monkeypatch):
    # When policy + few-shot have content, both are appended to the base prompt.
    from cs_intent_classifier_pkg import learning  # type: ignore[attr-defined]
    monkeypatch.setattr(learning, "build_few_shot_block", lambda env, n=0: "## Few-shot corrections\n- a→b\n")
    monkeypatch.setattr(learning, "build_policy_rules_block", lambda: "## Distilled policy rules\n- ADJUST: foo\n")
    out = classifier._augment_prompt_with_learning("BASE PROMPT", env="TEST")
    assert "BASE PROMPT" in out
    assert "Few-shot corrections" in out
    assert "Distilled policy rules" in out


def test_augment_prompt_no_blocks_returns_base(monkeypatch):
    from cs_intent_classifier_pkg import learning  # type: ignore[attr-defined]
    monkeypatch.setattr(learning, "build_few_shot_block", lambda env, n=0: "")
    monkeypatch.setattr(learning, "build_policy_rules_block", lambda: "")
    out = classifier._augment_prompt_with_learning("BASE", env="TEST")
    assert out == "BASE"


def test_augment_prompt_learning_failure_does_not_break(monkeypatch):
    # If learning raises, the prompt is returned unchanged (classify never breaks).
    from cs_intent_classifier_pkg import learning  # type: ignore[attr-defined]
    def boom(env, n=0):
        raise RuntimeError("db locked")
    monkeypatch.setattr(learning, "build_few_shot_block", boom)
    monkeypatch.setattr(learning, "build_policy_rules_block", lambda: "")
    out = classifier._augment_prompt_with_learning("BASE", env="TEST")
    assert out == "BASE"


# ── Conversation-closing detection ──

def test_closing_email_pure_thanks():
    ge = _kw("Re: Order tracking", "Thank you so much for your help!")
    assert ge is not None
    assert ge["is_conversation_closing"] is True
    assert ge["in_scope"] is True
    assert ge["route"] == "auto_handle"
    assert ge["urgency"] == "low"
    assert ge["emotion"]["value"] == "grateful"
    assert ge["primary_intent"] == "spam_irrelevant"
    # Must validate against schema
    validated = GateExtract.model_validate(ge)
    assert validated.is_conversation_closing is True


def test_closing_email_got_it_thanks():
    ge = _kw("Re: Swatches", "Got it thanks!")
    assert ge is not None
    assert ge["is_conversation_closing"] is True
    assert ge["in_scope"] is True


def test_closing_email_not_triggered_when_question_present():
    # Thank-you + new question → NOT closing, falls through to other classification
    ge = _kw("Re: Order", "Thank you for the update! When will my order #12345678 arrive?")
    # Should be classified as logistics (question marker "when" + order number)
    assert ge is not None
    assert ge["is_conversation_closing"] is False
    assert ge["primary_intent"] == "logistics_inquiry"


def test_closing_email_not_triggered_with_question_mark():
    ge = _kw("Re: Refund", "Thanks for the help? But I still need a refund.")
    assert ge is not None
    assert ge["is_conversation_closing"] is False


def test_closing_email_with_issue_keyword_not_triggered():
    # "thanks" + "damage" → has a real issue, not closing
    ge = _kw("Re: Damaged sofa", "Thanks for reaching out, the damage is still an issue though.")
    assert ge is not None
    assert ge["is_conversation_closing"] is False


def test_is_closing_email_helper_direct():
    assert classifier._is_closing_email(subject="Re: Help", body="Thank you for your help!") is True
    assert classifier._is_closing_email(subject="Re: Order", body="Thanks!") is True
    assert classifier._is_closing_email(subject="Re: Q", body="Thank you, but when will it ship?") is False
    assert classifier._is_closing_email(subject="", body="") is False


def test_schema_includes_is_conversation_closing_default_false():
    ge = _kw("Tracking", "Where is my order #11223344?")
    assert ge is not None
    assert ge["is_conversation_closing"] is False
    validated = GateExtract.model_validate(ge)
    assert validated.is_conversation_closing is False


# ── Emotion null coercion (regression: LLM returns emotion sub-fields as null) ──

def test_coerce_llm_nulls_emotion_subfields():
    raw = {"emotion": {"value": None, "confidence": None}, "intents": [], "fabrication_guard": True}
    out = classifier._coerce_llm_nulls(raw)
    assert out["emotion"]["value"] == "neutral"
    assert out["emotion"]["confidence"] == "low"


def test_coerce_llm_nulls_is_conversation_closing():
    raw = {"is_conversation_closing": None, "intents": [], "fabrication_guard": True}
    out = classifier._coerce_llm_nulls(raw)
    assert out["is_conversation_closing"] is False


def test_coerce_llm_nulls_emotion_whole_dict_null():
    raw = {"emotion": None, "intents": [], "fabrication_guard": True}
    out = classifier._coerce_llm_nulls(raw)
    assert out["emotion"]["value"] == "neutral"
    assert out["emotion"]["confidence"] == "low"


# ── Conversation history passthrough ──

def test_llm_classify_includes_conversation_history_in_user_msg(monkeypatch):
    """conversation_history is injected into the LLM user message JSON."""
    monkeypatch.setenv("CS_INTENT_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("CS_INTENT_LLM_MODEL", "fake-model")

    captured = {}

    def fake_call_llm(cfg, messages, timeout):
        captured["messages"] = messages
        # Return a valid minimal gate_extract JSON
        return '{"intents":[{"intent":"order_management","in_scope":false,"confidence":"high","urgency":"medium","snippet":"change address"}],"primary_intent":"order_management","in_scope":false,"route":"escalate","urgency":"medium","emotion":{"value":"neutral","confidence":"medium"},"language":{"value":"en","confidence":0.95},"customer_region":{"country":null,"province_state":null,"source":"unknown","confidence":"low"},"summary_zh":"客户要求改地址","fabrication_guard":true}'

    monkeypatch.setattr(classifier, "_call_llm", fake_call_llm)

    history = [
        {"role": "agent", "text": "Your order ships July 10."},
        {"role": "customer", "text": "Ok, I want to change the address."},
    ]
    classifier.llm_classify(
        subject="Re: Order", body="Ok change address please",
        metadata={}, conversation_history=history,
    )
    user_msg = json.loads(captured["messages"][1]["content"])
    assert "conversation_history" in user_msg
    assert len(user_msg["conversation_history"]) == 2
    assert user_msg["conversation_history"][0]["role"] == "agent"


def test_keyword_classify_ignores_conversation_history():
    """Keyword layer operates on subject+body only; history is not used."""
    ge = classifier.keyword_classify(
        subject="Where is my order #12345678?",
        body="Tracking please",
        metadata={},
    )
    assert ge is not None
    assert ge["primary_intent"] == "logistics_inquiry"
    # keyword_classify doesn't accept conversation_history — it's not in the signature


def test_classify_passes_history_to_llm(monkeypatch):
    """When keyword misses, classify() forwards conversation_history to llm_classify."""
    monkeypatch.setenv("CS_INTENT_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("CS_INTENT_LLM_MODEL", "fake-model")

    captured = {}

    def fake_call_llm(cfg, messages, timeout):
        captured["user_msg"] = messages[1]["content"]
        return '{"intents":[{"intent":"order_management","in_scope":false,"confidence":"high","urgency":"medium","snippet":"x"}],"primary_intent":"order_management","in_scope":false,"route":"escalate","urgency":"medium","emotion":{"value":"neutral","confidence":"medium"},"language":{"value":"en","confidence":0.95},"customer_region":{"country":null,"province_state":null,"source":"unknown","confidence":"low"},"summary_zh":"x","fabrication_guard":true}'

    monkeypatch.setattr(classifier, "_call_llm", fake_call_llm)

    # Subject that won't match any keyword pattern → falls through to LLM
    history = [{"role": "agent", "text": "Your order is confirmed."}]
    classifier.classify(
        subject="Re: Follow up",
        body="Actually I changed my mind about this",
        metadata={},
        conversation_history=history,
    )
    user_msg = json.loads(captured["user_msg"])
    assert len(user_msg["conversation_history"]) == 1
    assert user_msg["conversation_history"][0]["role"] == "agent"


# ── Scheme 3: keyword tier ──

def test_keyword_tier_default_all(monkeypatch):
    monkeypatch.delenv("CS_INTENT_KEYWORD_TIER", raising=False)
    assert classifier.keyword_tier() == "all"
    assert classifier.soft_keyword_enabled() is True


def test_keyword_tier_safe_only(monkeypatch):
    monkeypatch.setenv("CS_INTENT_KEYWORD_TIER", "safe_only")
    assert classifier.keyword_tier() == "safe_only"
    assert classifier.soft_keyword_enabled() is False


def test_safe_only_skips_logistics_falls_through(monkeypatch):
    monkeypatch.setenv("CS_INTENT_KEYWORD_TIER", "safe_only")
    ge = _kw("Where is my order?", "Where is my order #12345678?")
    assert ge is None


def test_safe_only_still_matches_threat(monkeypatch):
    monkeypatch.setenv("CS_INTENT_KEYWORD_TIER", "safe_only")
    ge = _kw("Lawyer", "I will contact my lawyer if you don't refund me.")
    assert ge is not None
    assert ge["threat_signal"] == "legal"


def test_safe_only_still_matches_closing(monkeypatch):
    monkeypatch.setenv("CS_INTENT_KEYWORD_TIER", "safe_only")
    ge = _kw("Re: Help", "Thank you for your help!")
    assert ge is not None
    assert ge["is_conversation_closing"] is True


# ── Scheme 2: soft guards ──

def test_logistics_guard_rejects_bare_order_number():
    ge = _kw("Hello", "Regarding order #11223344 please advise.")
    if ge is not None and ge["primary_intent"] == "logistics_inquiry":
        raise AssertionError("bare order number must not keyword-classify as logistics")


def test_product_guard_rejects_refund_conflict():
    ge = _kw("Sofa", "What are the dimensions? Also I want a refund for damage.")
    if ge is not None:
        assert ge["primary_intent"] != "product_inquiry"


def test_spam_guard_skips_greeting_with_re_subject():
    ge = _kw("Re: Order #99887766 tracking", "ok")
    assert ge is None or ge["primary_intent"] != "spam_irrelevant" or ge.get("is_conversation_closing")


def test_overlay_forces_logistics_fallthrough(monkeypatch):
    monkeypatch.setattr(
        classifier,
        "_load_keyword_overlays",
        lambda: [
            {
                "id": "t1",
                "action": "fallthrough",
                "blocks": ["logistics", "soft"],
                "pattern": r"\bwhere is my order\b",
            }
        ],
    )
    ge = _kw("Where is my order?", "Where is my order #12345678?")
    assert ge is None
