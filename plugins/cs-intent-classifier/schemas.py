"""Pydantic models for the cs-intent-classifier gate_extract schema.

These models are the canonical contract between the classifier module and its
consumers (cs-ops-bridge seams, Console frontend, learning loop). They encode
the confirmed decisions:

- Multi-intent multi-order: ``intents`` is a list, each item independent.
- No-fabrication: every field that cannot be determined is null and listed in
  ``null_fields`` / ``uncertain_fields``; ``fabrication_guard`` self-asserts.
- customer_region reliability tiers via ``source``.
- Five-class taxonomy with after_sale/order_management/spam out-of-scope.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# ── Enums (kept as plain str + validators to avoid enum serialization friction) ──

INTENT_ENUMS = (
    "product_inquiry",
    "logistics_inquiry",
    "after_sale_issue",
    "order_management",
    "spam_irrelevant",
)
ROUTE_ENUMS = ("auto_handle", "escalate", "review")
URGENCY_ENUMS = ("low", "medium", "high")
CONFIDENCE_ENUMS = ("high", "medium", "low")
EMOTION_ENUMS = ("calm", "frustrated", "angry", "anxious", "grateful", "neutral")
REGION_SOURCE_ENUMS = ("order_address", "visitor_geo", "email_mention", "email_tld", "unknown")
THREAT_ENUMS = ("legal", "social", "executive", None)
CUSTOMER_SEGMENT_ENUMS = ("new", "returning", "vip", "b2b", "unknown")
CONVERSATION_STAGE_ENUMS = ("first_contact", "follow_up", "unknown")


class ProductRef(BaseModel):
    """A product mentioned in the email. slug must come from email text or metadata — never inferred."""

    slug: Optional[str] = None
    name: Optional[str] = None
    line: Optional[str] = None
    confidence: str = Field(default="medium", pattern="^(high|medium|low)$")


class IntentItem(BaseModel):
    """One detected intent in a multi-intent email."""

    intent: str
    in_scope: bool
    confidence: str = Field(default="medium", pattern="^(high|medium|low)$")
    related_orders: list[str] = Field(default_factory=list)
    related_products: list[ProductRef] = Field(default_factory=list)
    post_sale_signal: Optional[dict[str, Any]] = None  # {damaged/refund/return/replace: bool, type: str}
    urgency: str = Field(default="medium", pattern="^(low|medium|high)$")
    snippet: str = ""


class CustomerRegion(BaseModel):
    """Customer region with reliability source. null country when no reliable signal."""

    country: Optional[str] = None
    province_state: Optional[str] = None
    source: str = Field(default="unknown", pattern="^(order_address|visitor_geo|email_mention|email_tld|unknown)$")
    confidence: str = Field(default="low", pattern="^(high|medium|low)$")


class EmotionSignal(BaseModel):
    value: str = Field(default="neutral", pattern="^(calm|frustrated|angry|anxious|grateful|neutral)$")
    confidence: str = Field(default="medium", pattern="^(high|medium|low)$")


class LanguageSignal(BaseModel):
    value: str = Field(default="en", pattern="^(en|zh|other)$")
    confidence: float = Field(default=0.99, ge=0.0, le=1.0)


class GateExtract(BaseModel):
    """The full classification output, persisted and injected into agent briefs."""

    # Multi-intent
    intents: list[IntentItem] = Field(default_factory=list)
    primary_intent: str = "spam_irrelevant"
    in_scope: bool = False
    route: str = Field(default="review", pattern="^(auto_handle|escalate|review)$")
    urgency: str = Field(default="medium", pattern="^(low|medium|high)$")

    # Signals
    emotion: EmotionSignal = Field(default_factory=EmotionSignal)
    language: LanguageSignal = Field(default_factory=LanguageSignal)
    products: list[ProductRef] = Field(default_factory=list)
    orders: list[str] = Field(default_factory=list)
    customer_region: CustomerRegion = Field(default_factory=CustomerRegion)
    customer_segment: str = Field(default="unknown", pattern="^(new|returning|vip|b2b|unknown)$")
    summary_zh: str = ""
    hindsight_keywords: list[str] = Field(default_factory=list)

    # Extra high-value signals
    conversation_stage: str = Field(default="unknown", pattern="^(first_contact|follow_up|unknown)$")
    response_template_hint: Optional[str] = None
    attachment_hint: bool = False
    pii_flag: bool = False
    ambiguous: bool = False
    needs_clarification: Optional[str] = None
    threat_signal: Optional[str] = Field(default=None, pattern="^(legal|social|executive)$")

    # Provenance + no-fabrication
    model_version: str = "v1"
    classifier_source: str = Field(default="keyword", pattern="^(keyword|llm)$")
    uncertain_fields: list[str] = Field(default_factory=list)
    null_fields: list[str] = Field(default_factory=list)
    fabrication_guard: bool = True


# ── HTTP request/response bodies ──


class ClassifyRequest(BaseModel):
    """Inbound classify request from the cs-ops-bridge seam (or CLI)."""

    session_id: str
    env: str = "LIVE"
    message_id: str = ""
    subject: str = ""
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClassifyResponse(BaseModel):
    session_id: str
    env: str
    classified_at: str
    gate_extract: GateExtract


class IntentOverrideItem(BaseModel):
    """One intent override in a correction."""

    intent: str
    in_scope: Optional[bool] = None
    reason: str = ""


class CorrectionRequest(BaseModel):
    """Operator correction from Console."""

    env: str = "LIVE"
    operator_id: str
    primary_intent: Optional[str] = None
    intent_overrides: list[IntentOverrideItem] = Field(default_factory=list)
    reason: str = ""


class IntentReadResponse(BaseModel):
    session_id: str
    env: str
    predicted: Optional[GateExtract] = None
    corrected: Optional[GateExtract] = None
    corrections: list[dict[str, Any]] = Field(default_factory=list)
