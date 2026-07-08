"""Deterministic intent classification for inbound QuickCEP messages."""

from __future__ import annotations

import re
from typing import Any

# Keywords mapped to escalation (non-exhaustive; agent may override with reason).
_ESCALATE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(vip|loyal customer|repeat buyer)\b.*\b(discount|15%|20%|special price)\b", "vip_discount"),
    (r"\b(refund|chargeback|dispute)\b.*\b(\$?\d{3,}|\d{4,})\b", "high_value_refund"),
    (r"\b(refund|chargeback)\b", "refund_request"),
    (r"\b(lawyer|legal|ftc|bbb|sue|lawsuit|attorney)\b", "legal_threat"),
    (r"\b(tiktok|instagram|twitter|x\.com|social media).*\b(post|expose|review)\b", "social_threat"),
    (r"\b(executive|manager|supervisor)\b.*\b(speak|talk|call)\b", "executive_demand"),
    (r"\b(b2b|wholesale|trade|bulk order)\b", "b2b_inquiry"),
)

_AUTO_HANDLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(where is|track(ing)?|shipping|delivery|shipment|order status)\b", "logistics"),
    (r"\b(order\s*#?\s*\d{10,})\b", "logistics"),
    (r"\b(dimension|size|material|color|spec|weight|assembly|sku|product)\b", "product"),
    # Fabric/material/swatch/custom order inquiries — common pre-sale product questions
    (r"\b(fabric|swatch|sample|leather|suede|linen|velvet|chenille|upholstery|napuck|nubuck)\b", "product"),
    (r"\b(custom\s*order|customized|made\s*to\s*order|special\s*order)\b", "product"),
    (r"\b(in\s*stock|availability|lead\s*time|when\s*available)\b", "product"),
    (r"\b(showroom|in\s*person|see\s*it\s*in\s*person)\b", "product"),
    (r"\b(missing part|wrong item|damaged|scratch|broken)\b", "issue_standard"),
)


def classify_intent(
    *,
    subject: str = "",
    body: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return routing decision for a customer message.

    Returns:
        dict with keys: route (auto_handle|escalate|review), category, confidence, matched_rule
    """
    text = f"{subject}\n{body}".lower()
    meta = metadata or {}

    for pattern, category in _ESCALATE_PATTERNS:
        if re.search(pattern, text, re.I):
            return {
                "route": "escalate",
                "category": category,
                "confidence": "high",
                "matched_rule": pattern,
            }

    for pattern, category in _AUTO_HANDLE_PATTERNS:
        if re.search(pattern, text, re.I):
            return {
                "route": "auto_handle",
                "category": category,
                "confidence": "medium",
                "matched_rule": pattern,
            }

    if meta.get("force_escalate"):
        return {
            "route": "escalate",
            "category": "forced",
            "confidence": "high",
            "matched_rule": "metadata.force_escalate",
        }

    return {
        "route": "review",
        "category": "unclear",
        "confidence": "low",
        "matched_rule": None,
    }
