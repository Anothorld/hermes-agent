"""povison-cs dedicated Hindsight knowledge tools (retain/recall) for cs-ops-bridge.

Self-contained module: HTTP-direct to Hindsight, structured dual-domain metadata,
refine-then-retain with PII redaction, Intent+Attribute Parser, credibility/time
rerank. No dependency on Hermes HindsightMemoryProvider.

Bank isolation hard constraint: bank_id is always the Knowledge bank
(`CS_OPS_HINDSIGHT_KNOWLEDGE_BANK`, default `furniture-knowledge`); never the
Experience bank. Tools do not accept a `bank` override.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# --- Config (env-driven, no secrets hardcoded) ---
HINDSIGHT_BASE_URL = os.environ.get("HINDSIGHT_BASE_URL", "http://192.168.10.123:8888").rstrip("/")
HINDSIGHT_API_KEY = os.environ.get("HINDSIGHT_API_KEY", "")
KNOWLEDGE_BANK = (os.environ.get("CS_OPS_HINDSIGHT_KNOWLEDGE_BANK") or "furniture-knowledge").strip() or "furniture-knowledge"
API_PREFIX = "/v1/default"
HTTP_TIMEOUT = float(os.environ.get("CS_HINDSIGHT_HTTP_TIMEOUT", "300"))

# LLM (optional; rule fallback when absent)
LLM_BASE_URL = os.environ.get("CS_HINDSIGHT_LLM_BASE_URL", "https://zenmux.ai/api/v1").rstrip("/")
LLM_API_KEY = os.environ.get("CS_HINDSIGHT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("CS_HINDSIGHT_LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = float(os.environ.get("CS_HINDSIGHT_LLM_TIMEOUT", "60"))

DOMAIN_PRODUCT = "product"
DOMAIN_POLICY = "policy"
DOMAIN_BOTH = "both"

SOURCE_ORDER = {"official_pdf": 1.0, "official_policy": 1.0, "human_confirmed": 0.8, "user_reported": 0.5}
POLICY_TYPES = {"return", "warranty", "shipping", "installation", "payment"}
# Extended set the deployed bank accepts (entities_allow_free_form=true); plan O13.2 uses `swatch`.
POLICY_TYPES_EXTENDED = POLICY_TYPES | {"swatch"}
SKIP_REASONS = {"one_off_compensation", "order_specific_exception", "session_narrative", "no_product_or_policy_fact"}

# --- Reference attribute vocabulary (non-mandatory Parser normalization, plan P0.5) ---
_VOCAB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hindsight_attribute_vocab.json")
_vocab_cache: dict[str, Any] | None = None


def _load_vocab() -> dict[str, Any]:
    global _vocab_cache
    if _vocab_cache is not None:
        return _vocab_cache
    try:
        with open(_VOCAB_PATH, "r", encoding="utf-8") as f:
            _vocab_cache = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("hindsight_attribute_vocab.json unavailable: %s", exc)
        _vocab_cache = {}
    return _vocab_cache


def normalize_attribute(attribute: str, question: str = "") -> tuple[str, bool]:
    """Map a free-form attribute (or a synonym found in the question) to a canonical
    attribute name from the reference vocabulary. Returns (canonical_or_passthrough,
    is_known). Unknown attributes pass through unchanged with is_known=False so the
    Parser keeps dynamic discovery (plan P0.5: 非强制，未知原样透传并标记)."""
    vocab = _load_vocab()
    attrs = vocab.get("attributes", []) if isinstance(vocab, dict) else []
    if not attrs:
        return (attribute or ""), True
    target = (attribute or "").strip().lower().replace(" ", "_")
    ql = (question or "").lower()
    for entry in attrs:
        canon = entry.get("attribute", "")
        if target and target == canon:
            return canon, True
        for syn in entry.get("synonyms", []):
            if (target and target == syn.lower().replace(" ", "_")) or (ql and syn.lower() in ql):
                return canon, True
    return (attribute or ""), False

# --- PII patterns ---
_PII_PATTERNS = {
    "order_id": re.compile(r"\b(?:order|ord|po)\s?#?\s?\d{5,}\b|(?<!\w)#\d{6,}\b", re.I),
    "customer_email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}\b"),
    "tracking": re.compile(r"\b(?:1Z|tracking#?|trk#?|tn#?)[A-Z0-9]{8,}\b", re.I),
}


def scan_pii(text: str) -> list[str]:
    """Return list of PII field types found in text."""
    hits: list[str] = []
    for name, pat in _PII_PATTERNS.items():
        if pat.search(text or ""):
            hits.append(name)
    return hits


def heuristic_redact(text: str) -> tuple[str, list[str]]:
    """Replace PII patterns with [REDACTED:<field>]; return (text, redacted_fields)."""
    redacted: list[str] = []
    out = text or ""
    for name, pat in _PII_PATTERNS.items():
        if pat.search(out):
            out = pat.sub(f"[REDACTED:{name}]", out)
            redacted.append(name)
    return out, redacted


# --- KnowledgeObject schema (dual-domain, per plan contract) ---
class KnowledgeObject(BaseModel):
    domain: str = Field(..., description="product | policy")
    product_id: str | None = None
    product_name: str | None = None
    attribute: str | None = None
    page: str = ""
    policy_type: str | None = None
    applies_to: str = "all"
    version: str = ""
    effective_from: str = ""
    effective_to: str = ""
    category: str
    source: str
    confirmed: str = "true"
    evidence_doc: str = ""
    aliases: list[str] = Field(default_factory=list)
    question: str
    answer: str
    key_fact: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.9
    reusable: bool = True
    skip_reason: str | None = None
    redacted_fields: list[str] = Field(default_factory=list)
    env: str = "TEST"
    created_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_retain_content(ko: KnowledgeObject) -> str:
    """Build the searchable content: header + Narrative + Q/A (plan O7)."""
    header_parts = [f"[domain:{ko.domain}]"]
    if ko.domain == DOMAIN_PRODUCT:
        if ko.product_id:
            header_parts.append(f"[product:{ko.product_id}]")
        if ko.attribute:
            header_parts.append(f"[attr:{ko.attribute}]")
    else:
        if ko.policy_type:
            header_parts.append(f"[policy_type:{ko.policy_type}]")
        if ko.applies_to:
            header_parts.append(f"[applies_to:{ko.applies_to}]")
    alias_str = " | ".join(ko.aliases) if ko.aliases else ""
    header = " ".join(header_parts)
    if alias_str:
        header += f" aliases: {alias_str}"
    lines = [header, f"Narrative: {ko.key_fact}"]
    if ko.domain == DOMAIN_PRODUCT and ko.product_name:
        lines.append(f"Product: {ko.product_name}")
    lines.append(f"Question: {ko.question}")
    lines.append(f"Answer: {ko.answer}")
    lines.append(f"Key Fact: {ko.key_fact}")
    return "\n".join(lines)


def _build_entities(ko: KnowledgeObject) -> list[dict[str, str]]:
    """Explicit entities with domain prefix, aligned with typed entity_labels."""
    ents: list[dict[str, str]] = []
    if ko.domain == DOMAIN_PRODUCT:
        if ko.product_id:
            ents.append({"text": f"product:{ko.product_id}", "type": "product"})
        if ko.attribute:
            ents.append({"text": f"attribute:{ko.attribute}", "type": "attribute"})
    else:
        if ko.policy_type:
            ents.append({"text": f"policy_type:{ko.policy_type}", "type": "policy_type"})
        if ko.applies_to:
            ents.append({"text": f"applies_to:{ko.applies_to}", "type": "applies_to"})
    for a in ko.aliases[:1]:
        ents.append({"text": a, "type": "CONCEPT"})
    return ents


def _build_tags(ko: KnowledgeObject) -> list[str]:
    """Stable dimension tags only; no session/escalation ids (plan O4/P0.2)."""
    tags: list[str] = []
    if ko.domain == DOMAIN_PRODUCT and ko.product_id:
        tags.append(f"product:{ko.product_id}")
    elif ko.domain == DOMAIN_POLICY and ko.policy_type:
        tags.append(f"policy:{ko.policy_type}")
    if ko.category:
        tags.append(f"category:{ko.category}")
    return tags


def _stringify_metadata(ko: KnowledgeObject) -> dict[str, str]:
    """All metadata values must be strings (OpenAPI hard constraint, plan O1)."""
    meta: dict[str, str] = {
        "domain": ko.domain,
        "source": ko.source,
        "confirmed": ko.confirmed,
        "evidence_doc": ko.evidence_doc,
        "page": ko.page,
        "aliases": "|".join(ko.aliases),
        "question": ko.question,
        "answer": ko.answer,
        "key_fact": ko.key_fact,
        "evidence": ",".join(ko.evidence),
        "confidence": f"{ko.confidence}",
        "reusable": str(ko.reusable).lower(),
        "redacted_fields": ",".join(ko.redacted_fields),
        "env": ko.env,
    }
    if ko.domain == DOMAIN_PRODUCT:
        meta["product_id"] = ko.product_id or ""
        meta["product_name"] = ko.product_name or ""
        meta["attribute"] = ko.attribute or ""
    else:
        meta["policy_type"] = ko.policy_type or ""
        meta["applies_to"] = ko.applies_to or ""
        meta["version"] = ko.version
        meta["effective_from"] = ko.effective_from
        meta["effective_to"] = ko.effective_to
    meta["category"] = ko.category
    # ko_json: full KO as JSON string for ops/debug
    meta["ko_json"] = json.dumps(ko.model_dump(), ensure_ascii=False)
    # enforce all-string
    return {k: str(v) for k, v in meta.items()}


def build_retain_item(ko: KnowledgeObject) -> dict[str, Any]:
    """Build one MemoryItem for HTTP retain."""
    return {
        "content": format_retain_content(ko),
        "context": "povison product knowledge" if ko.domain == DOMAIN_PRODUCT else "povison policy knowledge",
        "tags": _build_tags(ko),
        "timestamp": "unset",
        "observation_scopes": "shared",
        "entities": _build_entities(ko),
        "metadata": _stringify_metadata(ko),
    }


def build_retain_payload(ko: KnowledgeObject, *, async_: bool = True) -> dict[str, Any]:
    return {"async": async_, "items": [build_retain_item(ko)]}


# --- LLM helper (OpenAI-compatible; rule fallback when no key) ---
def _llm_json(prompt: str) -> dict[str, Any] | None:
    """Call LLM with a JSON instruction; parse JSON object from response. None on failure."""
    if not LLM_API_KEY:
        return None
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You output ONLY a single JSON object, no prose, no code fences."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    try:
        with httpx.Client(timeout=LLM_TIMEOUT, headers=headers) as c:
            r = c.post(f"{LLM_BASE_URL}/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        # strip code fences if present
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        return json.loads(text)
    except Exception as e:
        log.warning("hindsight_ko LLM call failed: %s", e)
        return None


_REFINE_PROMPT = """You refine a customer-service Q&A into a reusable, PII-free knowledge fact.
De-identify: remove order ids, emails, phone numbers, customer names, tracking ids from question/answer/key_fact.
Judge domain: "product" (has SKU + spec/material/cert/dimension/compatibility) or "policy" (no SKU; return/warranty/shipping/installation/payment/swatch/showroom/COI).
Output JSON with EXACTLY these keys (all string values except aliases list and confidence number and reusable bool):
reusable (bool), skip_reason (null or one of: one_off_compensation, order_specific_exception, session_narrative, no_product_or_policy_fact), domain ("product"|"policy"), product_id, product_name, attribute, category, policy_type, applies_to, version, effective_from, effective_to, source, confirmed, evidence_doc, page, question, answer, key_fact, aliases (list[str]), confidence (number).
- If the answer is a one-off compensation/$ goodwill/this-order-only exception or pure session narrative, set reusable=false and skip_reason.
- question: rewrite to a model-level reusable phrasing (drop order context).
- answer: de-identified, self-contained.
- key_fact: one coherent sentence with the core fact.
- aliases: synonymous phrasings customers use (for recall).
- source: one of official_pdf, official_policy, human_confirmed, user_reported.
- category: one of assembly,dimensions,materials,color,shipping-policy,return-policy,warranty,swatch-sample,showroom,coi,white-glove,custom-order,compatibility,pricing,other.

Raw inputs:
question: {q}
answer: {a}
sku: {sku}
product_name: {pname}
product_slug: {pslug}
escalation_id: {esc}
session_id: {sid}
source: {src}
env: {env}
"""


def _rule_fallback_refine(raw_q: str, raw_a: str, sku: str | None, product_name: str | None,
                           product_slug: str | None, escalation_id: str | None,
                           session_id: str | None, env: str, source: str) -> KnowledgeObject:
    """Heuristic refine when LLM unavailable: redact PII, infer domain, keep original phrasing as alias."""
    q, q_red = heuristic_redact(raw_q)
    a, a_red = heuristic_redact(raw_a)
    redacted = sorted(set(q_red + a_red))
    has_sku = bool(sku)
    key_fact = a.strip() or q.strip()
    # category heuristics
    ql = raw_q.lower()
    if any(k in ql for k in ("swatch", "sample", "color chip", "色卡", "fabric sample", "leather sample")):
        category = "swatch-sample"
    elif any(k in ql for k in ("return", "refund", "退换", "退货")):
        category = "return-policy"
    elif any(k in ql for k in ("warranty", "质保", "guarantee")):
        category = "warranty"
    elif any(k in ql for k in ("shipping", "delivery", "物流", "配送", "white glove", "wgd")):
        category = "shipping-policy"
    elif any(k in ql for k in ("install", "安装")):
        category = "other"
    elif any(k in ql for k in ("showroom", "展厅")):
        category = "showroom"
    elif any(k in ql for k in ("coi", "certificate of insurance")):
        category = "coi"
    else:
        category = "other"
    # policy_type heuristics (5-value enum + swatch per plan O13.2; bank accepts free-form)
    ptype = ""
    if any(k in ql for k in ("swatch", "sample", "color chip", "色卡", "fabric sample", "leather sample")):
        ptype = "swatch"
    for pt in POLICY_TYPES:
        if pt in ql:
            ptype = pt
            break
    if not ptype and any(k in ql for k in ("return", "refund", "退换", "退货")):
        ptype = "return"
    if not ptype and any(k in ql for k in ("warranty", "质保", "guarantee")):
        ptype = "warranty"
    if not ptype and any(k in ql for k in ("shipping", "delivery", "物流", "配送")):
        ptype = "shipping"
    if not ptype and any(k in ql for k in ("install", "安装")):
        ptype = "installation"
    if not ptype and ("payment" in ql or "支付" in ql):
        ptype = "payment"
    # Plan O13.1 rule 3: SKU + policy_type → domain=both (refine_to_ko splits into 2 KOs).
    # SKU alone → product; policy_type alone → policy; neither → policy (safer default,
    # avoids polluting product recall with non-product text).
    if has_sku and ptype:
        domain = DOMAIN_BOTH
    elif has_sku:
        domain = DOMAIN_PRODUCT
    else:
        domain = DOMAIN_POLICY
    aliases = []
    # crude alias extraction: split question on punctuation, drop PII-bearing fragments
    for part in re.split(r"[?,\n]", raw_q):
        p = part.strip()
        if not (2 <= len(p) <= 40):
            continue
        p_red, _ = heuristic_redact(p)
        if "[REDACTED" in p_red or scan_pii(p):
            continue
        key = p_red.lower()
        if key not in aliases:
            aliases.append(key)
    if not aliases:
        aliases = [heuristic_redact(raw_q.strip()[:40])[0].lower()]
    evidence = [x for x in [f"escalation_id:{escalation_id}" if escalation_id else None,
                            f"session_id:{session_id}" if session_id else None] if x]
    return KnowledgeObject(
        domain=domain,
        product_id=sku or None,
        product_name=product_name or None,
        attribute=None,
        category=category,
        policy_type=ptype or None,
        applies_to="all",
        source=source,
        confirmed="true",
        evidence_doc=f"escalation_id:{escalation_id}" if escalation_id else "",
        aliases=aliases,
        question=q,
        answer=a,
        key_fact=key_fact,
        evidence=evidence,
        confidence=0.5,
        reusable=True,
        redacted_fields=redacted,
        env=env,
        created_at=_now_iso(),
    )


def _looks_like_one_off_compensation(text: str) -> bool:
    """Rule fallback: $N + goodwill/compensation/refund without general policy wording."""
    if not re.search(r"\$\s?\d+|\b\d+\s?(dollars|usd)\b|补偿|退款|goodwill|compensation", text, re.I):
        return False
    general = re.search(r"warranty window|standard policy|return window|return period|policy applies|all customers|any order", text, re.I)
    return not bool(general)


def _is_empty_or_narrative_only(ko: KnowledgeObject) -> bool:
    text = (ko.answer + " " + ko.key_fact).strip()
    if len(text) < 15:
        return True
    return bool(re.fullmatch(r"[\s.,;]*(已回复|已发送|草稿已保存|waiting|pending|draft saved|replied|sent)[\s.,;]*", text, re.I))


def _split_both_domain(ko: KnowledgeObject) -> list[KnowledgeObject]:
    """If ko.domain == both, split into two KOs (one product, one policy) per plan O13.1 rule 3
    (禁止揉成一条). Otherwise return [ko]. Each split KO reuses the refined text."""
    if ko.domain != DOMAIN_BOTH:
        return [ko]
    product_fields = {f: getattr(ko, f) for f in ("product_id", "product_name", "attribute", "page")}
    policy_fields = {f: getattr(ko, f) for f in ("policy_type", "applies_to", "version", "effective_from", "effective_to")}
    shared = {f: getattr(ko, f) for f in (
        "category", "source", "confirmed", "evidence_doc", "aliases", "question", "answer", "key_fact",
        "evidence", "confidence", "reusable", "skip_reason", "redacted_fields", "env", "created_at")}
    ko_product = KnowledgeObject(domain=DOMAIN_PRODUCT, **product_fields, **{k: v for k, v in policy_fields.items() if k in ("applies_to",)},
                                **shared)
    # product KO: applies_to defaults to "all"; clear policy fields
    ko_product.policy_type = None
    ko_product.version = ""
    ko_product.effective_from = ""
    ko_product.effective_to = ""
    ko_policy = KnowledgeObject(domain=DOMAIN_POLICY, **{k: v for k, v in product_fields.items() if k == "page"},
                                applies_to=ko.applies_to or "all", policy_type=ko.policy_type,
                                version=ko.version, effective_from=ko.effective_from, effective_to=ko.effective_to,
                                **shared)
    ko_policy.product_id = None
    ko_policy.product_name = None
    ko_policy.attribute = None
    return [ko_product, ko_policy]


def refine_to_ko(*, raw_question: str, raw_answer: str, sku: str | None = None,
                 product_name: str | None = None, product_slug: str | None = None,
                 escalation_id: str | None = None, session_id: str | None = None,
                 env: str = "TEST", source: str = "human_confirmed",
                 force_retain: bool = False) -> dict[str, Any]:
    """Run refine pipeline. Returns one of:
    - {'kos': [KnowledgeObject, ...]} on success (list has 2 entries when domain=both, else 1)
    - {'status':'skipped','reason':..., 'detail':...} when the refined content is not reusable
      or contains residual PII.

    Per plan O13.1 rule 3, a domain=both fact (SKU + policy_type) is split into TWO KOs
    (product + policy) so each domain retains independently — never mashed into one item."""
    prompt = _REFINE_PROMPT.format(q=raw_question, a=raw_answer, sku=sku or "", pname=product_name or "",
                                   pslug=product_slug or "", esc=escalation_id or "", sid=session_id or "",
                                   src=source, env=env)
    llm = _llm_json(prompt)
    if llm is not None:
        try:
            # de-identify again defensively if LLM missed
            q, q_red = heuristic_redact(llm.get("question", raw_question))
            a, a_red = heuristic_redact(llm.get("answer", raw_answer))
            kf, kf_red = heuristic_redact(llm.get("key_fact", ""))
            redacted = sorted(set((llm.get("redacted_fields") or []) + q_red + a_red + kf_red))
            domain = llm.get("domain") or (DOMAIN_PRODUCT if sku else DOMAIN_POLICY)
            # sanitize policy_type: bank accepts free-form; allow the extended set, drop garbage
            ptype_raw = (llm.get("policy_type") or "").strip().lower()
            ptype = ptype_raw if ptype_raw in POLICY_TYPES_EXTENDED else None
            ko = KnowledgeObject(
                domain=domain,
                product_id=llm.get("product_id") or sku,
                product_name=llm.get("product_name") or product_name,
                attribute=llm.get("attribute") or None,
                page=llm.get("page") or "",
                policy_type=ptype,
                applies_to=llm.get("applies_to") or "all",
                version=llm.get("version") or "",
                effective_from=llm.get("effective_from") or "",
                effective_to=llm.get("effective_to") or "",
                category=llm.get("category") or "other",
                source=llm.get("source") or source,
                confirmed=llm.get("confirmed") or "true",
                evidence_doc=llm.get("evidence_doc") or (f"escalation_id:{escalation_id}" if escalation_id else ""),
                aliases=llm.get("aliases") or [],
                question=q, answer=a, key_fact=kf,
                evidence=[x for x in [f"escalation_id:{escalation_id}" if escalation_id else None,
                                     f"session_id:{session_id}" if session_id else None] if x],
                confidence=float(llm.get("confidence", 0.8)),
                reusable=bool(llm.get("reusable", True)),
                skip_reason=llm.get("skip_reason"),
                redacted_fields=redacted,
                env=env, created_at=_now_iso(),
            )
        except Exception as e:
            log.warning("hindsight_ko LLM refine parse failed (%s); using rule fallback", e)
            ko = _rule_fallback_refine(raw_question, raw_answer, sku, product_name, product_slug,
                                       escalation_id, session_id, env, source)
    else:
        ko = _rule_fallback_refine(raw_question, raw_answer, sku, product_name, product_slug,
                                   escalation_id, session_id, env, source)

    # Reusable judgment (rule fallback acts on refined text).
    # force_retain bypasses ONLY the reusability gates (one_off_compensation /
    # session_narrative / reusable=false) — it is an ops override to rescue a
    # mis-skipped fact. PII is a HARD constraint (bank directive O9: never store
    # customer PII), so the residual PII scan below runs unconditionally.
    if not force_retain and not ko.reusable:
        return {"status": "skipped", "reason": "not_reusable", "detail": ko.skip_reason or "unspecified"}
    if not force_retain and _looks_like_one_off_compensation(ko.answer + " " + ko.key_fact):
        return {"status": "skipped", "reason": "not_reusable", "detail": "one_off_compensation"}
    if not force_retain and _is_empty_or_narrative_only(ko):
        return {"status": "skipped", "reason": "not_reusable", "detail": "session_narrative"}

    # PII residual scan on refined text — HARD gate, never bypassed by force_retain.
    residual = scan_pii(ko.question + " " + ko.answer + " " + ko.key_fact)
    if residual:
        return {"status": "skipped", "reason": "residual_pii", "detail": ",".join(sorted(set(residual)))}

    # Plan O13.1 rule 3: domain=both must split into TWO KOs (product + policy).
    kos = _split_both_domain(ko)
    return {"kos": kos}


# --- Intent+Attribute Parser (recall side) ---
_PARSER_PROMPT = """Parse a customer question into structured recall filters.
Output JSON with EXACTLY these keys (all strings): domain ("product"|"policy"|"both"), product_id, attribute, policy_type (one of return|warranty|shipping|installation|payment|swatch, or ""), applies_to.
- domain="product" if the question is about a specific product's spec/material/cert/dimension/compatibility.
- domain="policy" if about returns/warranty/shipping/installation/payment/swatch/showroom/COI without a specific product.
- domain="both" if it asks about a product AND a policy (e.g. "can I return sofa X?").
- product_id = SKU if given.
- attribute = the non-standard attribute asked about (e.g. material_certification, leg_count, weight_capacity).
- policy_type = policy category if policy/both; use "swatch" for physical sample/color chip mailing questions.
- applies_to = "all" or "category:sofa" or "product:<SKU>".

question: {q}
sku_hint: {sku}
product_name_hint: {pname}
"""


def parse_query(*, question: str, sku: str | None = None, product_name: str | None = None) -> dict[str, str]:
    """Intent+Attribute Parser. Returns dict with domain/product_id/attribute/policy_type/applies_to."""
    prompt = _PARSER_PROMPT.format(q=question, sku=sku or "", pname=product_name or "")
    llm = _llm_json(prompt)
    if llm is not None:
        attr_raw = llm.get("attribute") or ""
        attr, attr_known = normalize_attribute(attr_raw, question)
        return {
            "domain": llm.get("domain") or (DOMAIN_PRODUCT if sku else DOMAIN_POLICY),
            "product_id": llm.get("product_id") or (sku or ""),
            "attribute": attr,
            "attribute_known": attr_known,  # plan P0.5: unknown attributes flagged for review
            "policy_type": llm.get("policy_type") or "",
            "applies_to": llm.get("applies_to") or "all",
        }
    # rule fallback
    q = question.lower()
    has_sku = bool(sku)
    ptype = ""
    for pt in POLICY_TYPES:
        if pt in q:
            ptype = pt
            break
    if "swatch" in q or "sample" in q or "color chip" in q or "色卡" in q:
        ptype = ptype or "swatch"
    if "return" in q or "refund" in q or "退货" in q or "退换" in q:
        ptype = ptype or "return"
    if "warranty" in q or "质保" in q or "guarantee" in q:
        ptype = ptype or "warranty"
    if "shipping" in q or "delivery" in q or "物流" in q or "配送" in q:
        ptype = ptype or "shipping"
    if "install" in q or "安装" in q:
        ptype = ptype or "installation"
    if "payment" in q or "支付" in q:
        ptype = ptype or "payment"
    domain = DOMAIN_PRODUCT if has_sku and not ptype else (DOMAIN_POLICY if ptype and not has_sku else (DOMAIN_BOTH if has_sku and ptype else DOMAIN_POLICY))
    attr, attr_known = normalize_attribute("", question)
    return {"domain": domain, "product_id": sku or "", "attribute": attr,
            "attribute_known": attr_known, "policy_type": ptype, "applies_to": "all"}


def build_recall_request(*, parsed: dict[str, str], question: str,
                         max_tokens: int = 4096, budget: str = "mid",
                         use_metadata_filter: bool = True) -> dict[str, Any]:
    """Build recall request body per plan R3/O13.4."""
    domain = parsed.get("domain") or DOMAIN_POLICY
    parts: list[str] = []
    q_lower = question.lower()
    if domain in (DOMAIN_PRODUCT, DOMAIN_BOTH) and parsed.get("product_id"):
        sku = parsed["product_id"]
        if sku.lower() not in q_lower:
            parts.append(sku)
    if domain in (DOMAIN_POLICY, DOMAIN_BOTH) and parsed.get("policy_type"):
        pt = parsed["policy_type"].replace("-", " ")
        if pt not in q_lower:
            parts.append(pt)
    if parsed.get("attribute"):
        attr = parsed["attribute"].replace("_", " ")
        if attr not in q_lower:
            parts.append(attr)
    parts.append(question)
    query = " ".join(parts)
    body: dict[str, Any] = {
        "query": query,
        "max_tokens": max_tokens,
        "budget": budget,
        "types": ["observation", "world", "experience"],
        "prefer_observations": True,
    }
    # tags + tags_match=all to lock single product / single policy type
    tags: list[str] = []
    if domain in (DOMAIN_PRODUCT, DOMAIN_BOTH) and parsed.get("product_id"):
        tags.append(f"product:{parsed['product_id']}")
    if domain in (DOMAIN_POLICY, DOMAIN_BOTH) and parsed.get("policy_type"):
        tags.append(f"policy:{parsed['policy_type']}")
    if tags:
        body["tags"] = tags
        body["tags_match"] = "all"
    # metadata_filter (P1 supported on 0.8.4; degrade if backend bug)
    # Per plan R3/O13.4: filter only on domain + product_id (product) or domain +
    # policy_type (policy). attribute is a query-enrichment hint (BM25/semantic),
    # NOT a hard filter — retain-side attribute is often null/loose, so filtering
    # on it would over-constrain and miss real facts.
    if use_metadata_filter:
        mf: dict[str, str] = {}
        if domain in (DOMAIN_PRODUCT, DOMAIN_BOTH):
            mf["domain"] = DOMAIN_PRODUCT
            if parsed.get("product_id"):
                mf["product_id"] = parsed["product_id"]
        elif domain == DOMAIN_POLICY:
            mf["domain"] = DOMAIN_POLICY
            if parsed.get("policy_type"):
                mf["policy_type"] = parsed["policy_type"]
        if mf:
            body["metadata_filter"] = mf
    return body


def _result_source_score(meta: dict[str, Any] | None) -> float:
    if not meta:
        return 0.5
    src = (meta.get("source") or "").lower()
    return SOURCE_ORDER.get(src, 0.5)


def rerank_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Credibility + time rerank with per-key dedup.

    Policy: drop expired effective_to; per policy_type keep the LATEST effective_from
    (plan 1129 — a newer version supersedes older ones; credibility is the tiebreaker).
    Product: per (product_id, attribute) keep the highest credibility source (plan 1130).
    Facts without a dedup key are kept as-is. Survivors are then displayed in credibility
    order (plan 1128)."""
    if not results:
        return results
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def meta(r: dict[str, Any]) -> dict[str, Any]:
        return r.get("metadata") or {}

    # 1. drop expired policy versions
    kept = [r for r in results if not ((meta(r).get("effective_to") or "").strip() and (meta(r)["effective_to"]).strip() < today)]

    policy = [r for r in kept if (meta(r).get("domain") or "").strip().lower() == DOMAIN_POLICY and (meta(r).get("policy_type") or "").strip()]
    product = [r for r in kept if (meta(r).get("domain") or "").strip().lower() == DOMAIN_PRODUCT and (meta(r).get("product_id") or "").strip()]
    other = [r for r in kept if r not in policy and r not in product]

    # 2. policy dedup: latest effective_from wins (credibility tiebreaker)
    policy.sort(key=lambda r: ((meta(r).get("effective_from") or "").strip(), _result_source_score(meta(r)), r.get("final_score") or r.get("score") or 0.0), reverse=True)
    seen_p: set[str] = set()
    policy_dedup: list[dict[str, Any]] = []
    for r in policy:
        k = str(meta(r)["policy_type"]).strip()
        if k in seen_p:
            continue
        seen_p.add(k)
        policy_dedup.append(r)

    # 3. product dedup: highest credibility wins per (product_id, attribute)
    product.sort(key=lambda r: (_result_source_score(meta(r)), r.get("final_score") or r.get("score") or 0.0), reverse=True)
    seen_pd: set[tuple] = set()
    product_dedup: list[dict[str, Any]] = []
    for r in product:
        k = (str(meta(r)["product_id"]).strip(), (meta(r).get("attribute") or "").strip())
        if k in seen_pd:
            continue
        seen_pd.add(k)
        product_dedup.append(r)

    # 4. display order: credibility first, then recency
    out = policy_dedup + product_dedup + other
    out.sort(key=lambda r: (-_result_source_score(meta(r)), (meta(r).get("effective_from") or "").strip(), -(r.get("final_score") or r.get("score") or 0.0)))
    return out


# --- HTTP layer ---
def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if HINDSIGHT_API_KEY:
        h["Authorization"] = f"Bearer {HINDSIGHT_API_KEY}"
    return h


def http_retain(payload: dict[str, Any], *, bank: str = KNOWLEDGE_BANK) -> dict[str, Any]:
    """POST /v1/default/banks/{bank}/memories. Returns parsed JSON or {'error':...}."""
    url = f"{HINDSIGHT_BASE_URL}{API_PREFIX}/banks/{bank}/memories"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=_headers()) as c:
            r = c.post(url, json=payload)
            if r.status_code >= 400:
                return {"error": f"HTTP {r.status_code}", "detail": r.text[:500], "bank": bank}
            return r.json()
    except Exception as e:
        return {"error": "retain_exception", "detail": str(e), "bank": bank}


def http_recall(body: dict[str, Any], *, bank: str = KNOWLEDGE_BANK) -> dict[str, Any]:
    """POST /v1/default/banks/{bank}/memories/recall."""
    url = f"{HINDSIGHT_BASE_URL}{API_PREFIX}/banks/{bank}/memories/recall"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=_headers()) as c:
            r = c.post(url, json=body)
            if r.status_code >= 400:
                return {"error": f"HTTP {r.status_code}", "detail": r.text[:500], "bank": bank}
            return r.json()
    except Exception as e:
        return {"error": "recall_exception", "detail": str(e), "bank": bank}


def retain_ko(*, raw_question: str, raw_answer: str, sku: str | None = None,
              product_name: str | None = None, product_slug: str | None = None,
              escalation_id: str | None = None, session_id: str | None = None,
              env: str = "TEST", source: str = "human_confirmed",
              force_retain: bool = False, async_: bool = True) -> dict[str, Any]:
    """Full retain pipeline: refine -> PII -> retain to Knowledge bank.

    Per plan O13.1 rule 3, a domain=both fact (SKU + policy_type) is split into TWO
    KOs and each is retained independently — so product facts and policy facts land
    as separate memory items with their own domain-prefixed tags/entities/metadata.
    Returns {'status':'retained','bank':..., 'kos':[...], 'http':...} (or skipped/error)."""
    refined = refine_to_ko(raw_question=raw_question, raw_answer=raw_answer, sku=sku,
                          product_name=product_name, product_slug=product_slug,
                          escalation_id=escalation_id, session_id=session_id,
                          env=env, source=source, force_retain=force_retain)
    if "kos" not in refined:
        return refined  # skipped
    kos: list[KnowledgeObject] = refined["kos"]
    retained: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    all_redacted: list[str] = []
    for ko in kos:
        payload = build_retain_payload(ko, async_=async_)
        resp = http_retain(payload)
        all_redacted.extend(ko.redacted_fields)
        if "error" in resp:
            errors.append({"ko": ko.model_dump(), "http": resp})
        else:
            retained.append({"ko": ko.model_dump(), "http": resp})
    if not retained and errors:
        return {"status": "error", "bank": KNOWLEDGE_BANK, "kos": [e["ko"] for e in errors], "http": errors[0]["http"]}
    return {"status": "retained", "bank": KNOWLEDGE_BANK,
            "redacted_fields": sorted(set(all_redacted)),
            "kos": [r["ko"] for r in retained], "http": [r["http"] for r in retained]}


def recall_ko(*, question: str, sku: str | None = None, product_name: str | None = None,
              max_tokens: int = 4096, budget: str = "mid",
              use_metadata_filter: bool = True) -> dict[str, Any]:
    """Full recall pipeline: parse -> recall -> rerank. Degrades on backend errors (R7).

    For domain=both (e.g. "can I return sofa X?"), split into a product recall and a
    policy recall, then merge — a single request with tags_match=all would require
    BOTH product and policy tags on one fact and return nothing (plan 1124)."""
    parsed = parse_query(question=question, sku=sku, product_name=product_name)

    def _one_recall(forced_domain: str) -> tuple[dict[str, Any], dict[str, Any]]:
        p = {**parsed, "domain": forced_domain}
        b = build_recall_request(parsed=p, question=question, max_tokens=max_tokens,
                                 budget=budget, use_metadata_filter=use_metadata_filter)
        r = http_recall(b)
        # R7 degradation: if metadata_filter caused a 5xx, retry without it
        if "error" in r and use_metadata_filter and "metadata_filter" in b:
            log.warning("recall(%s) with metadata_filter failed (%s); retrying without filter",
                        forced_domain, r.get("detail"))
            # Pass an explicit empty dict, not omit, so the engine's auto-derive
            # (query_analyzer SKU extraction → metadata_filter) does NOT re-inject
            # the same filter the retry is trying to drop. ``{}`` is a SQL no-op and
            # skips auto-derivation (the engine only auto-derives when the param is
            # None / omitted).
            b2 = {k: v for k, v in b.items() if k != "metadata_filter"}
            b2["metadata_filter"] = {}
            r = http_recall(b2)
        return b, r

    if parsed.get("domain") == DOMAIN_BOTH:
        body_p, resp_p = _one_recall(DOMAIN_PRODUCT)
        body_l, resp_l = _one_recall(DOMAIN_POLICY)
        if "error" in resp_p and "error" in resp_l:
            return {"status": "error", "bank": KNOWLEDGE_BANK, "parsed": parsed,
                    "request": {"product": body_p, "policy": body_l},
                    "http": {"product": resp_p, "policy": resp_l}}
        results = (resp_p.get("results") or []) + (resp_l.get("results") or [])
        body = {"product": body_p, "policy": body_l}
    else:
        body = build_recall_request(parsed=parsed, question=question, max_tokens=max_tokens,
                                    budget=budget, use_metadata_filter=use_metadata_filter)
        resp = http_recall(body)
        if "error" in resp and use_metadata_filter and "metadata_filter" in body:
            log.warning("recall with metadata_filter failed (%s); retrying without filter", resp.get("detail"))
            # Pass an explicit empty dict, not omit, so the engine's auto-derive
            # (query_analyzer SKU extraction → metadata_filter) does NOT re-inject
            # the same filter the retry is trying to drop. ``{}`` is a SQL no-op and
            # skips auto-derivation (the engine only auto-derives when the param is
            # None / omitted).
            body2 = {k: v for k, v in body.items() if k != "metadata_filter"}
            body2["metadata_filter"] = {}
            resp = http_recall(body2)
        if "error" in resp:
            return {"status": "error", "bank": KNOWLEDGE_BANK, "parsed": parsed, "request": body, "http": resp}
        results = resp.get("results") or []
    reranked = rerank_results(results)
    return {"status": "ok", "bank": KNOWLEDGE_BANK, "parsed": parsed, "request": body,
            "count": len(reranked), "results": reranked}


def knowledge_bank_id() -> str:
    """Expose the hardcoded Knowledge bank id (bank isolation)."""
    return KNOWLEDGE_BANK
