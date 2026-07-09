"""Hybrid intent classifier: keyword pre-filter + LLM fallback.

Keyword layer: deterministic regex on subject+body, zero LLM cost, high precision
for clear single-intent cases (order tracking, refund/legal threats, B2B spam).
Returns a full gate_extract dict when confident, or None to fall through to LLM.

LLM layer: OpenAI-compatible chat completions call with the versioned prompt from
config/intent_prompt_v1.md. Outputs full multi-intent schema. Self-configured via
CS_INTENT_LLM_* env vars — does NOT read profile config.

No-fabrication contract is enforced by the prompt; the LLM layer additionally
validates the response (fabrication_guard=true, no empty intents, parses JSON).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import yaml

from .schemas import GateExtract

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent / "config"


# ── Keyword patterns (ported/extended from cs-ops-bridge/classify_intent.py) ──

# Threat / escalation patterns → force escalate, mark threat_signal or after_sale out_of_scope.
_THREAT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(lawyer|legal|ftc|bbb|sue|lawsuit|attorney)\b", "legal"),
    (r"\b(tiktok|instagram|twitter|x\.com|social media).*\b(post|expose|review)\b", "social"),
    (r"\b(executive|manager|supervisor)\b.*\b(speak|talk|call)\b", "executive"),
)

# After-sale patterns → after_sale_issue, out_of_scope.
_AFTER_SALE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"\b(refund|chargeback|dispute)\b", "refund", "refund"),
    (r"\b(return|send back|exchange)\b", "return", "return"),
    (r"\b(damaged|broken|ripped|scratch|crack|defect|missing part|wrong item)\b", "damage", "damage"),
    (r"\b(replacement|replace)\b", "replace", "replace"),
    (r"\b(warranty|guarantee claim)\b", "warranty", "warranty"),
)

# Order management patterns → order_management, out_of_scope.
_ORDER_MGMT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(cancel( my| the)? order|cancellation)\b", "cancel"),
    (r"\b(change|update|modify).*(address|shipping|color|colour|size|model)\b", "modify"),
    (r"\b(coupon|promo|discount).*(not work|didn't apply|incorrect)\b", "payment"),
)

# Checkout / BNPL payment failure → order_management (NOT after_sale_issue).
# "Afterpay" / "after pay" is a payment method, not post-purchase "after sale".
_CHECKOUT_PAYMENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(after\s?pay|afterpay|klarna|affirm|zip\s?pay|bnpl)\b", "checkout_payment"),
    (
        r"\b(payment|pay(?:ment)?)\s+(?:was\s+)?(?:not\s+)?"
        r"(?:approved|declined|failed|rejected|denied)\b",
        "checkout_payment",
    ),
    (
        r"\b(not\s+approved|declined|was\s+denied|wasn't\s+approved)\b.*"
        r"(?:after\s?pay|afterpay|payment|checkout)\b",
        "checkout_payment",
    ),
    (
        r"\b(?:after\s?pay|afterpay|payment)\b.*"
        r"\b(?:not\s+approved|declined|was\s+denied|wasn't\s+approved)\b",
        "checkout_payment",
    ),
    (
        r"\b(didn'?t|couldn'?t|could\s+not|cannot|can'?t)\s+"
        r"(?:finish|complete)\s+(?:the\s+)?(?:sale|purchase|checkout|order|payment)\b",
        "checkout_payment",
    ),
    (
        r"\b(?:finish|complete)\s+(?:the\s+)?(?:sale|purchase|checkout)\b.*"
        r"(?:after\s?pay|afterpay|payment)\b",
        "checkout_payment",
    ),
    (r"\b(checkout|check\s+out)\s+(?:failed|issue|problem|error)\b", "checkout_payment"),
    (r"\b(unable\s+to\s+pay|can'?t\s+pay|cannot\s+pay)\b", "checkout_payment"),
)

# Auto-handle patterns → in_scope intents.
_LOGISTICS_PATTERNS: tuple[str, ...] = (
    r"\b(where is|track(ing)?|shipping|delivery|shipment|order status)\b",
    r"\b(order\s*#?\s*\d{6,})\b",
    r"\b(when will|eta|estimated.*arrival)\b",
)
_PRODUCT_PATTERNS: tuple[str, ...] = (
    r"\b(dimensions?|size|material|materials?|color|colour|spec|specs|weight|assembly|sku)\b",
    r"\b(in stock|availability|lead time)\b",
    r"\b(recommend|suggestion).*(sofa|table|chair|desk|bed)\b",
    # Fabric/material/swatch/custom order — common pre-sale product questions
    r"\b(fabric|swatch|sample|leather|suede|linen|velvet|chenille|upholstery|nubuck|napuck)\b",
    r"\b(custom\s*order|customized|made\s*to\s*order|special\s*order)\b",
    r"\b(showroom|in\s*person|see\s*it\s*in\s*person)\b",
)

# Spam patterns → spam_irrelevant, out_of_scope.
_SPAM_PATTERNS: tuple[str, ...] = (
    r"\b(guest post|seo service|backlink|partnership proposal)\b",
    r"\b(we are a (supplier|manufacturer|factory))\b",
    r"^(hi|hello|ok|yes|no problem)\s*\.?\s*$",
)

# Conversation-closing patterns → is_conversation_closing=true, in_scope=true.
# Pure thank-you / acknowledgment with NO new question. Distinct from spam: a
# closing email is from a real customer in an existing thread signaling "we're
# done". The agent should send a brief "you're welcome" and close the session.
# These patterns match the thank-you phrase; question-marker exclusion + length
# cap in _is_closing_email ensures we don't misclassify a real inquiry.
_CLOSING_PATTERNS: tuple[str, ...] = (
    r"\b(thank you|thanks|thx|appreciate it|much appreciated|got it thanks|"
    r"perfect thanks|that answers my question|that helps thanks|"
    r"thank you for your help|thanks for the help|great thanks|"
    r"you're welcome|no thanks needed)\b",
)
_CLOSING_MAX_LEN = 200  # closing emails are short; longer = likely a real inquiry
# Question markers that disqualify closing detection (email has a new ask).
_QUESTION_MARKERS = re.compile(
    r"\b(how|what|when|where|why|which|can you|could you|would you|do you|"
    r"is there|are there|will you|may i|can i|need|want|looking for|"
    r"question|help me|issue|problem|order|tracking|refund|return|damage|"
    r"cancel|change|update|missing|wrong|broken)\b",
    re.I,
)

_ORDER_RE = re.compile(r"\b(?:order\s*#?\s*)(\d{6,})\b|#(\d{6,})\b", re.I)
_SKU_RE = re.compile(r"\b(SF|SR|OA|BD|DK|NF|CT|WD|OT)[-_]?\d{3,6}\b", re.I)


def _current_model_version() -> str:
    """Read current production model_version from config/intent_version.txt."""
    path = _CONFIG_DIR / "intent_version.txt"
    try:
        return path.read_text(encoding="utf-8").strip() or "v1"
    except OSError:
        return "v1"


def _load_scope() -> dict[str, bool]:
    """Load in_scope whitelist from config/intent_scope.yaml."""
    path = _CONFIG_DIR / "intent_scope.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {k: bool(v) for k, v in data.items()}
    except OSError:
        return {
            "product_inquiry": True,
            "logistics_inquiry": True,
            "after_sale_issue": False,
            "order_management": False,
            "spam_irrelevant": False,
        }


def _extract_orders(text: str) -> list[str]:
    out: list[str] = []
    for m in _ORDER_RE.finditer(text):
        num = m.group(1) or m.group(2)
        if num:
            out.append(num)
    seen: set[str] = set()
    deduped: list[str] = []
    for n in out:
        if n not in seen:
            seen.add(n)
            deduped.append(n)
    return deduped


def _extract_skus(text: str) -> list[str]:
    return [m.group(0).upper() for m in _SKU_RE.finditer(text)]


def _detect_language(text: str) -> tuple[str, float]:
    """Cheap language detection: CJK ratio → zh; else en (default for Povison NA market)."""
    if not text:
        return "en", 0.5
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total = max(len(text), 1)
    ratio = cjk / total
    if ratio > 0.15:
        return "zh", min(0.99, 0.7 + ratio)
    return "en", 0.95


def _detect_emotion(text: str) -> tuple[str, str]:
    """Cheap emotion detection. Returns (value, confidence)."""
    lower = text.lower()
    angry_markers = ("furious", "outrageous", "unacceptable", "disgusting", "rip off", "scam")
    frustrated_markers = ("still no", "again", "every time", "never", "tired of", "frustrated", "disappointed")
    anxious_markers = ("worried", "concerned", "anxious", "hope", "please help", "urgent")
    grateful_markers = ("thank you so much", "appreciate", "great service", "love it", "amazing")
    if any(m in lower for m in angry_markers):
        return "angry", "high"
    if any(m in lower for m in frustrated_markers):
        return "frustrated", "medium"
    if any(m in lower for m in anxious_markers):
        return "anxious", "medium"
    if any(m in lower for m in grateful_markers):
        return "grateful", "medium"
    return "neutral", "medium"


def _build_region(metadata: dict[str, Any], body: str, customer_email: str) -> dict[str, Any]:
    """Build customer_region from metadata signals by priority. Never fabricates."""
    order_addrs = metadata.get("order_addresses") or []
    visitor_geo = metadata.get("visitor_geo") or {}

    # 1. order_address (highest)
    for addr in order_addrs:
        if isinstance(addr, dict) and addr.get("country"):
            return {
                "country": addr.get("country"),
                "province_state": addr.get("province_state"),
                "source": "order_address",
                "confidence": "high",
            }
    # 2. visitor_geo
    if visitor_geo.get("country"):
        return {
            "country": visitor_geo.get("country"),
            "province_state": visitor_geo.get("province_state"),
            "source": "visitor_geo",
            "confidence": "medium",
        }
    # 3. email_mention — scan body for explicit location statement
    m = re.search(r"\b(i am|i'm|located|live in|from)\s+([a-z ,]+)", body, re.I)
    if m:
        loc = m.group(2).strip().rstrip(".")
        return {"country": loc, "province_state": None, "source": "email_mention", "confidence": "medium"}
    # 4. email_tld — weak
    if customer_email:
        domain = customer_email.rsplit("@", 1)[-1].lower() if "@" in customer_email else ""
        tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
        tld_map = {"ca": "CA", "co.uk": "GB", "us": "US", "com": ""}
        if tld in tld_map and tld_map[tld]:
            return {"country": tld_map[tld], "province_state": None, "source": "email_tld", "confidence": "low"}
    # 5. unknown
    return {"country": None, "province_state": None, "source": "unknown", "confidence": "low"}


# ── Keyword layer ──


def keyword_classify(
    *,
    subject: str,
    body: str,
    metadata: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Deterministic keyword classifier. Returns full gate_extract dict or None.

    Returns None when the input doesn't match a clear single-intent pattern,
    signaling the caller to fall through to the LLM layer.
    """
    text = f"{subject}\n{body}"
    lower = text.lower()
    scope = _load_scope()
    orders = _extract_orders(text)
    skus = _extract_skus(text)
    customer_email = metadata.get("customer_email") or ""
    region = _build_region(metadata, body, customer_email)
    lang_val, lang_conf = _detect_language(text)
    emo_val, emo_conf = _detect_emotion(text)

    uncertain: list[str] = []
    null_fields: list[str] = []
    if region["source"] == "unknown":
        null_fields.append("customer_region")
    elif region["source"] == "email_tld":
        uncertain.append("customer_region")

    def _mk_intent(name: str, snippet: str, urgency: str = "medium", **kw: Any) -> dict[str, Any]:
        related_orders = kw.get("related_orders", orders if name in ("logistics_inquiry", "order_management", "after_sale_issue") else [])
        related_products = kw.get("related_products", [{"slug": s, "name": None, "line": None, "confidence": "high"} for s in skus] if skus else [])
        return {
            "intent": name,
            "in_scope": scope.get(name, False),
            "confidence": "high",
            "related_orders": related_orders,
            "related_products": related_products,
            "post_sale_signal": kw.get("post_sale_signal"),
            "urgency": urgency,
            "snippet": snippet[:300],
        }

    # Threat → escalate + after_sale out_of_scope
    for pat, threat_type in _THREAT_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent("after_sale_issue", _snippet_match(text, pat), urgency="high")
            return _assemble(
                intents=[intent],
                primary_intent="after_sale_issue",
                route="escalate",
                urgency="high",
                threat_signal=threat_type,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh=_summary_zh(threat_type, "threat", orders, skus),
                hindsight_keywords=_hindsight_keywords(skus, [threat_type]),
                metadata=metadata,
                source="keyword",
            )

    # Conversation-closing email (pure thank-you, no new question).
    # Checked before spam so a real customer's "thanks" in a thread is handled
    # as a closing acknowledgment, not discarded as spam.
    if _is_closing_email(subject=subject, body=body):
        intent = _mk_intent("spam_irrelevant", (subject + " " + body).strip()[:300], urgency="low")
        return _assemble(
            intents=[intent],
            primary_intent="spam_irrelevant",
            route="auto_handle",
            urgency="low",
            threat_signal=None,
            emotion_value="grateful",
            emotion_conf="high",
            lang_val=lang_val,
            lang_conf=lang_conf,
            orders=orders,
            skus=skus,
            region=region,
            uncertain=uncertain,
            null_fields=null_fields,
            summary_zh="客户表示感谢，话题结束",
            hindsight_keywords=[],
            metadata=metadata,
            source="keyword",
            is_conversation_closing=True,
        )

    # Spam
    for pat in _SPAM_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent("spam_irrelevant", _snippet_match(text, pat), urgency="low")
            return _assemble(
                intents=[intent],
                primary_intent="spam_irrelevant",
                route="review",
                urgency="low",
                threat_signal=None,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh="疑似垃圾/无关邮件",
                hindsight_keywords=[],
                metadata=metadata,
                source="keyword",
            )

    # Checkout / BNPL payment failure (before after_sale — "after pay" ≠ after sale).
    for pat, sub in _CHECKOUT_PAYMENT_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent("order_management", _snippet_match(text, pat), urgency="medium")
            return _assemble(
                intents=[intent],
                primary_intent="order_management",
                route="escalate",
                urgency="medium",
                threat_signal=None,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh=_summary_zh(sub, "order_mgmt", orders, skus),
                hindsight_keywords=_hindsight_keywords(skus, [sub, "payment"]),
                metadata=metadata,
                source="keyword",
            )

    # After-sale
    for pat, signal_type, _ in _AFTER_SALE_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent(
                "after_sale_issue",
                _snippet_match(text, pat),
                urgency="high",
                post_sale_signal={signal_type: True, "type": signal_type},
            )
            return _assemble(
                intents=[intent],
                primary_intent="after_sale_issue",
                route="escalate",
                urgency="high",
                threat_signal=None,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh=_summary_zh(signal_type, "after_sale", orders, skus),
                hindsight_keywords=_hindsight_keywords(skus, [signal_type]),
                metadata=metadata,
                source="keyword",
            )

    # Order management
    for pat, sub in _ORDER_MGMT_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent("order_management", _snippet_match(text, pat), urgency="medium")
            return _assemble(
                intents=[intent],
                primary_intent="order_management",
                route="escalate",
                urgency="medium",
                threat_signal=None,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh=_summary_zh(sub, "order_mgmt", orders, skus),
                hindsight_keywords=_hindsight_keywords(skus, [sub]),
                metadata=metadata,
                source="keyword",
            )

    # Logistics (in_scope) — only if clearly a tracking/shipping question
    for pat in _LOGISTICS_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent("logistics_inquiry", _snippet_match(text, pat), urgency="medium")
            return _assemble(
                intents=[intent],
                primary_intent="logistics_inquiry",
                route="auto_handle",
                urgency="medium",
                threat_signal=None,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh=_summary_zh("logistics", "logistics", orders, skus),
                hindsight_keywords=_hindsight_keywords(skus, ["tracking", "shipping"]),
                metadata=metadata,
                source="keyword",
            )

    # Product (in_scope)
    for pat in _PRODUCT_PATTERNS:
        if re.search(pat, lower):
            intent = _mk_intent("product_inquiry", _snippet_match(text, pat), urgency="low")
            return _assemble(
                intents=[intent],
                primary_intent="product_inquiry",
                route="auto_handle",
                urgency="low",
                threat_signal=None,
                emotion_value=emo_val,
                emotion_conf=emo_conf,
                lang_val=lang_val,
                lang_conf=lang_conf,
                orders=orders,
                skus=skus,
                region=region,
                uncertain=uncertain,
                null_fields=null_fields,
                summary_zh=_summary_zh("product", "product", orders, skus),
                hindsight_keywords=_hindsight_keywords(skus, ["product specs"]),
                metadata=metadata,
                source="keyword",
            )

    # No confident keyword match → fall through to LLM
    return None


def _snippet_match(text: str, pattern: str) -> str:
    """Extract a short snippet around the first regex match."""
    m = re.search(pattern, text, re.I)
    if not m:
        return text[:200]
    start = max(0, m.start() - 40)
    end = min(len(text), m.end() + 80)
    return text[start:end].strip()


def _is_closing_email(*, subject: str, body: str) -> bool:
    """Detect a pure thank-you / closing email with no new question or request.

    A closing email is a real customer's acknowledgment in an existing thread
    (e.g. "Thank you so much for your help!"). It is NOT spam — the agent should
    send a brief "you're welcome" and close the session. Returns False when the
    email contains question markers or substantive requests, even if it starts
    with "thanks".
    """
    # Check the body primarily (subject is usually "Re: ..." in a closing reply).
    # Fall back to subject only when body is empty.
    text = body.strip() if body.strip() else subject.strip()
    if not text:
        return False
    # Closing emails are short; a long body likely contains a real inquiry.
    if len(text) > _CLOSING_MAX_LEN:
        return False
    # Must match a closing pattern (thank-you / acknowledgment phrase).
    if not any(re.search(pat, text, re.I) for pat in _CLOSING_PATTERNS):
        return False
    # Must NOT contain a question or new request. Check only the body (not the
    # subject) — the subject of a closing reply is the thread context ("Re:
    # Order #123") and naturally contains "order"/"tracking" etc.
    if _QUESTION_MARKERS.search(text):
        return False
    # Must NOT contain a question mark (strong signal of a new ask).
    if "?" in text:
        return False
    return True


def _summary_zh(key: str, kind: str, orders: list[str], skus: list[str]) -> str:
    """Cheap deterministic Chinese summary for keyword-classified cases."""
    ord_str = f"订单#{','.join(orders)}" if orders else ""
    sku_str = f"商品{','.join(skus)}" if skus else ""
    ctx = " ".join(x for x in (ord_str, sku_str) if x).strip()
    table = {
        "threat": "客户提出威胁/法律诉求",
        "refund": "客户要求退款",
        "return": "客户要求退换货",
        "damage": "客户反馈商品破损",
        "replace": "客户要求换货",
        "warranty": "客户提出保修诉求",
        "cancel": "客户要求取消订单",
        "modify": "客户要求修改订单",
        "payment": "客户反馈支付/优惠券问题",
        "checkout_payment": "客户反馈结账/支付失败（含 Afterpay）",
        "logistics": "客户询问物流进度",
        "product": "客户咨询商品信息",
    }
    base = table.get(key, "客户来邮咨询")
    return f"{base}{('，涉及' + ctx) if ctx else ''}".strip()


def _hindsight_keywords(skus: list[str], extras: list[str]) -> list[str]:
    out: list[str] = []
    for s in skus:
        out.append(s)
        # add a lowercased line name guess for SKU prefixes (SF/SR)
        if s.startswith(("SF", "SR")):
            out.append(s.lower())
    out.extend(e.lower() for e in extras)
    return out


def _assemble(
    *,
    intents: list[dict[str, Any]],
    primary_intent: str,
    route: str,
    urgency: str,
    threat_signal: Optional[str],
    emotion_value: str,
    emotion_conf: str,
    lang_val: str,
    lang_conf: float,
    orders: list[str],
    skus: list[str],
    region: dict[str, Any],
    uncertain: list[str],
    null_fields: list[str],
    summary_zh: str,
    hindsight_keywords: list[str],
    metadata: dict[str, Any],
    source: str,
    is_conversation_closing: bool = False,
) -> dict[str, Any]:
    in_scope = any(i.get("in_scope") for i in intents)
    # Conversation-closing emails: force in_scope=True so the gate passes and
    # the agent sends an acknowledgment (not blocked as out-of-scope spam).
    if is_conversation_closing:
        in_scope = True
    products = [{"slug": s, "name": None, "line": None, "confidence": "high"} for s in skus]
    return {
        "intents": intents,
        "primary_intent": primary_intent,
        "in_scope": in_scope,
        "route": route,
        "urgency": urgency,
        "emotion": {"value": emotion_value, "confidence": emotion_conf},
        "language": {"value": lang_val, "confidence": lang_conf},
        "products": products,
        "orders": orders,
        "customer_region": region,
        "customer_segment": _segment(metadata),
        "summary_zh": summary_zh,
        "hindsight_keywords": hindsight_keywords,
        "conversation_stage": _stage(metadata),
        "response_template_hint": _template_hint(primary_intent),
        "attachment_hint": bool(re.search(r"\b(attach|photo|picture|image|screenshot)\b", summary_zh + " " + str(metadata), re.I)),
        "pii_flag": bool(orders or metadata.get("customer_email")),
        "ambiguous": False,
        "needs_clarification": None,
        "threat_signal": threat_signal,
        "is_conversation_closing": is_conversation_closing,
        "model_version": _current_model_version(),
        "classifier_source": source,
        "uncertain_fields": uncertain,
        "null_fields": null_fields,
        "fabrication_guard": True,
    }


def _segment(metadata: dict[str, Any]) -> str:
    prior = metadata.get("prior_session_count") or 0
    if metadata.get("customer_email") and "b2b" in str(metadata.get("intention_tags") or []).lower():
        return "b2b"
    if prior and int(prior) >= 5:
        return "vip"
    if prior and int(prior) >= 1:
        return "returning"
    if metadata.get("has_prior_session"):
        return "returning"
    return "new"


def _stage(metadata: dict[str, Any]) -> str:
    if metadata.get("has_prior_session"):
        return "follow_up"
    return "first_contact"


def _template_hint(primary: str) -> str:
    return {
        "logistics_inquiry": "logistics_tracking",
        "product_inquiry": "product_specs",
        "after_sale_issue": "after_sale_escalate",
        "order_management": "order_mgmt",
        "spam_irrelevant": "spam_skip",
    }.get(primary, "general")


# ── LLM layer ──


def _load_prompt() -> str:
    """Load the versioned prompt. File name follows intent_prompt_v{N}.md where N from version."""
    version = _current_model_version()
    path = _CONFIG_DIR / f"intent_prompt_{version}.md"
    if not path.exists():
        path = _CONFIG_DIR / "intent_prompt_v1.md"
    return path.read_text(encoding="utf-8")


def _llm_config() -> dict[str, str]:
    return {
        "provider": os.environ.get("CS_INTENT_LLM_PROVIDER", "").strip(),
        "model": os.environ.get("CS_INTENT_LLM_MODEL", "").strip(),
        "api_key": os.environ.get("CS_INTENT_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")).strip(),
        "base_url": os.environ.get("CS_INTENT_LLM_BASE_URL", "").strip(),
    }


def _llm_timeout() -> float:
    """LLM call timeout. Default 30s (classification prompts can be slow on
    self-hosted endpoints). Override via CS_INTENT_LLM_TIMEOUT."""
    try:
        return float(os.environ.get("CS_INTENT_LLM_TIMEOUT", "30"))
    except ValueError:
        return 30.0


def _augment_prompt_with_learning(prompt: str, *, env: str) -> str:
    """Append T2 few-shot + T3 policy blocks to the base prompt.

    Lazy-imports ``learning`` to keep classifier.py importable standalone (the
    learning module pulls in db which needs the sqlite path resolved). Both
    blocks are best-effort: empty/missing blocks are silently skipped so the
    classifier still works before any corrections/policy exist.
    """
    try:
        from . import learning
    except Exception:
        return prompt
    extras: list[str] = []
    try:
        fs = learning.build_few_shot_block(env=env)
        if fs:
            extras.append(fs)
    except Exception as exc:  # noqa: BLE001 — learning must never break classify
        log.debug("few-shot block build failed: %s", exc)
    try:
        pol = learning.build_policy_rules_block()
        if pol:
            extras.append(pol)
    except Exception as exc:  # noqa: BLE001
        log.debug("policy block build failed: %s", exc)
    if not extras:
        return prompt
    return prompt + "\n\n" + "\n\n".join(extras)


def llm_classify(
    *,
    subject: str,
    body: str,
    metadata: dict[str, Any],
    conversation_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """LLM fallback classifier. Calls OpenAI-compatible chat completions.

    If no LLM is configured (missing api_key/model), returns a conservative
    `review` gate_extract so the gate does not falsely pass or block. This keeps
    the module testable without an LLM key.

    The system prompt is augmented with two learning-loop blocks (when present):
    - T2 few-shot: recent operator corrections injected as in-context examples.
    - T3 policy: distilled rules from config/intent_policy.md.
    Both are built dynamically at call time so promotions take effect immediately.

    ``conversation_history`` (recent prior messages in the thread) is injected
    into the LLM user message so the model can understand reply context.
    """
    conversation_history = conversation_history or []
    cfg = _llm_config()
    if not cfg["api_key"] or not cfg["model"]:
        log.warning("CS_INTENT_LLM not configured — returning conservative review gate_extract")
        return _conservative_review(subject=subject, body=body, metadata=metadata)

    prompt = _load_prompt()
    prompt = _augment_prompt_with_learning(prompt, env=str(metadata.get("env") or "LIVE"))
    user_msg = json.dumps(
        {
            "subject": subject,
            "body": body,
            "metadata": _sanitize_metadata(metadata),
            "conversation_history": conversation_history,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_msg},
    ]
    llm_timeout = _llm_timeout()
    raw = _call_llm(cfg, messages, timeout=llm_timeout)
    if not raw:
        log.warning("LLM call failed — returning conservative review gate_extract")
        return _conservative_review(subject=subject, body=body, metadata=metadata)

    parsed = _parse_llm_json(raw)
    if parsed is None:
        log.warning("LLM returned unparseable JSON — returning conservative review gate_extract")
        return _conservative_review(subject=subject, body=body, metadata=metadata)

    # Enforce fabrication_guard self-check
    if parsed.get("fabrication_guard") is not True:
        raise FabricationError("LLM did not assert fabrication_guard=true")
    if not parsed.get("intents"):
        raise FabricationError("LLM returned empty intents")

    # Coerce null → schema defaults. The LLM correctly returns null for fields
    # it cannot determine (no-fabrication), but pydantic rejects null for list/
    # model fields. Normalize before validation.
    parsed = _coerce_llm_nulls(parsed)

    # Stamp provenance
    parsed["model_version"] = _current_model_version()
    parsed["classifier_source"] = "llm"
    # Validate via pydantic
    ge = GateExtract.model_validate(parsed)
    return ge.model_dump()


def _coerce_llm_nulls(d: dict[str, Any]) -> dict[str, Any]:
    """Convert LLM null outputs to schema-safe defaults (no-fabrication tolerant).

    The LLM returns null for unknown list/dict/sub-model fields. The pydantic
    schema requires list/dict types. This coerces null → empty list / default
    model so the no-fabrication behavior (null when unknown) flows through
    validation. Also coerces nested nulls inside each intent item.
    """
    # top-level list/dict fields that the LLM may null out
    for key in ("products", "orders", "hindsight_keywords", "uncertain_fields", "null_fields", "intents"):
        if d.get(key) is None:
            d[key] = []
    if d.get("customer_region") is None:
        d["customer_region"] = {"country": None, "province_state": None, "source": "unknown", "confidence": "low"}
        if "customer_region" not in (d.get("null_fields") or []):
            d.setdefault("null_fields", []).append("customer_region")
    if d.get("emotion") is None:
        d["emotion"] = {"value": "neutral", "confidence": "low"}
    # Emotion sub-fields may be null even when the dict exists (LLM returns
    # {"value": null, "confidence": null}). Coerce to defaults to prevent
    # pydantic ValidationError crashes.
    emo = d.get("emotion")
    if isinstance(emo, dict):
        if emo.get("value") is None:
            emo["value"] = "neutral"
        if emo.get("confidence") is None:
            emo["confidence"] = "low"
        d["emotion"] = emo
    if d.get("language") is None:
        d["language"] = {"value": "en", "confidence": 0.5}
    # string-with-default fields that the LLM may null out
    for key, default in (
        ("customer_segment", "unknown"),
        ("conversation_stage", "unknown"),
        ("primary_intent", "spam_irrelevant"),
        ("route", "review"),
        ("urgency", "medium"),
        ("summary_zh", ""),
        ("model_version", "v1"),
        ("classifier_source", "llm"),
    ):
        if d.get(key) is None:
            d[key] = default
    # bool fields that the LLM may null out → default False
    for key in ("is_conversation_closing", "ambiguous", "pii_flag", "attachment_hint", "fabrication_guard"):
        if d.get(key) is None:
            d[key] = False if key != "fabrication_guard" else True
    # language.confidence may come back as "low" string instead of float
    lang = d.get("language") or {}
    if isinstance(lang, dict):
        conf = lang.get("confidence")
        if isinstance(conf, str):
            try:
                lang["confidence"] = float(conf)
            except (ValueError, TypeError):
                lang["confidence"] = 0.5
        elif conf is None:
            lang["confidence"] = 0.5
        d["language"] = lang
    # coerce nulls inside intent items
    for i, it in enumerate(d.get("intents") or []):
        if isinstance(it, dict):
            for key in ("related_orders", "related_products"):
                if it.get(key) is None:
                    it[key] = []
            if it.get("post_sale_signal") is None:
                pass  # already Optional
    return d


class FabricationError(Exception):
    """Raised when the LLM output fails the no-fabrication contract."""


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Strip fields the LLM shouldn't see (raw PII we don't need to send)."""
    out = dict(metadata)
    visitor = out.get("visitor_geo") or {}
    if isinstance(visitor, dict) and visitor.get("ip"):
        visitor = {k: v for k, v in visitor.items() if k != "ip"}
        out["visitor_geo"] = visitor
    return out


def _call_llm(cfg: dict[str, str], messages: list[dict[str, str]], timeout: float) -> Optional[str]:
    """OpenAI-compatible chat completions POST. Returns assistant content or None."""
    base = cfg["base_url"] or "https://api.openai.com/v1"
    url = base.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {"model": cfg["model"], "messages": messages, "temperature": 0.0, "max_tokens": 1200},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        return (data.get("choices") or [{}])[0].get("message", {}).get("content")
    except urllib.error.HTTPError as exc:
        log.error("LLM HTTP %s: %s", exc.code, exc.read()[:200] if hasattr(exc, "read") else "")
        return None
    except Exception as exc:
        log.error("LLM call failed: %s", exc)
        return None


def _parse_llm_json(raw: str) -> Optional[dict[str, Any]]:
    """Parse LLM output, tolerating markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        # strip first fence line
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        # try to find the first {...} block
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _conservative_review(
    *,
    subject: str,
    body: str,
    metadata: dict[str, Any],
    conversation_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fallback when LLM unavailable: review route, no false pass/block."""
    text = f"{subject}\n{body}"
    orders = _extract_orders(text)
    skus = _extract_skus(text)
    region = _build_region(metadata, body, metadata.get("customer_email") or "")
    lang_val, lang_conf = _detect_language(text)
    emo_val, emo_conf = _detect_emotion(text)
    null_fields = ["customer_region"] if region["source"] == "unknown" else []
    uncertain = ["primary_intent", "intents", "route", "emotion.value", "language.value"]
    if region["source"] == "email_tld":
        uncertain.append("customer_region")
    return {
        "intents": [
            {
                "intent": "product_inquiry",
                "in_scope": True,
                "confidence": "low",
                "related_orders": orders,
                "related_products": [{"slug": s, "name": None, "line": None, "confidence": "low"} for s in skus],
                "post_sale_signal": None,
                "urgency": "medium",
                "snippet": text[:300],
            }
        ],
        "primary_intent": "product_inquiry",
        "in_scope": True,
        "route": "review",
        "urgency": "medium",
        "emotion": {"value": emo_val, "confidence": emo_conf},
        "language": {"value": lang_val, "confidence": lang_conf},
        "products": [{"slug": s, "name": None, "line": None, "confidence": "low"} for s in skus],
        "orders": orders,
        "customer_region": region,
        "customer_segment": _segment(metadata),
        "summary_zh": "无法确定意图，需人工复核",
        "hindsight_keywords": _hindsight_keywords(skus, ["review"]),
        "conversation_stage": _stage(metadata),
        "response_template_hint": "general",
        "attachment_hint": False,
        "pii_flag": bool(orders or metadata.get("customer_email")),
        "ambiguous": True,
        "needs_clarification": "LLM 不可用，需人工确认客户意图",
        "threat_signal": None,
        "model_version": _current_model_version(),
        "classifier_source": "keyword",
        "uncertain_fields": uncertain,
        "null_fields": null_fields,
        "fabrication_guard": True,
    }


# ── Top-level classify() ──


def classify(
    *,
    subject: str,
    body: str,
    metadata: dict[str, Any],
    conversation_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Hybrid classify: keyword first, LLM fallback. Returns a full gate_extract dict.

    The returned dict is validated against GateExtract and always has
    fabrication_guard=True. Raises FabricationError if the LLM output fails the
    no-fabrication contract and no conservative fallback is acceptable.

    ``conversation_history`` is only used by the LLM layer (keyword matching
    operates on the current email alone).
    """
    conversation_history = conversation_history or []
    t0 = time.monotonic()
    try:
        kw = keyword_classify(subject=subject, body=body, metadata=metadata)
        if kw is not None:
            log.debug("keyword layer classified (source=keyword)")
            return kw
        log.debug("keyword layer miss → LLM fallback")
        return llm_classify(
            subject=subject,
            body=body,
            metadata=metadata,
            conversation_history=conversation_history,
        )
    finally:
        log.debug("classify elapsed %.3fs", time.monotonic() - t0)
