#!/usr/bin/env python3
"""Migrate reusable facts from the Experience bank (povison-cs-hermes-user) to the
Knowledge bank (furniture-knowledge). Add-only — NEVER deletes from the source bank
(plan P2b / O10: deleting source memories triggers consolidation cascade).

Usage:
    python3 migrate_experience_to_knowledge.py --limit 50 --dry-run
    python3 migrate_experience_to_knowledge.py --limit 200 --offset 0 --type world
    python3 migrate_experience_to_knowledge.py --poll   # poll pending_consolidation

Filtering (skip non-reusable):
  - tags containing `session:*` (session narratives)
  - text matching one-off compensation / goodwill dollar amounts in order context
  - residual PII (order#/email/phone/tracking) after heuristic redaction

Each surviving fact is re-classified (domain inferred from text + entities) and retained
to the Knowledge bank via hindsight_ko.build_retain_payload + http_retain (async).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

# reuse the hindsight_ko module from the plugin root
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PLUGIN_ROOT)
import hindsight_ko as ko  # noqa: E402

EXP_BASE_URL = os.environ.get("CS_HINDSIGHT_EXPERIENCE_URL", "http://192.168.10.63:8888").rstrip("/")
EXP_BANK = os.environ.get("CS_HINDSIGHT_EXPERIENCE_BANK", "povison-cs-hermes-user")
API_PREFIX = "/v1/default"
LIST_TIMEOUT = 30

_COMPENSATION_RE = re.compile(r"(?i)\$\s?\d+\s*(goodwill|compensation|refund\s+amount|credit)|goodwill\s+credit|\$\d+\s*(?:as|for)\s+(?:apology|inconvenience)")
_SESSION_TAG_RE = re.compile(r"^session:")
# Agent-operational narrative patterns — these are internal process notes, NOT reusable
# product/policy customer-facing knowledge. Content-based (a fact may carry a session
# origin tag but still be reusable product knowledge).
_AGENT_NARRATIVE_RE = re.compile(
    r"(?i)\b(agent|draft-save|draft save|apply-handoff|apply handoff|pii_flag|escalat\w*|"
    r"handoff|inline\s+--content|inline\s+content|in_scope|out_of_scope|dispatch-conte|"
    r"join-chat|join chat|\bCAL\b|quickcep\s+path|internal\s+domain\s+guard|apply-handoff\s+with\s+phase|"
    r"agent\s+(must|should|uses|drafts|pre-classified)|carrier\s+coi|seo\s+pitch|"
    r"bridge\s+cli|terminal\s+tool|delegate_task|cs-intent-classifier|pre-classified\s+intents|"
    r"fabricat\w*|uncertain\s+or\s+null|tool\s+must\s+be\s+used|must\s+be\s+used\s+for|"
    r"intent\s+classifier|phase\s+(failed|skipped|completed))\b"
)
# Handling Experience (customer-emotion management / response strategy) — belongs in the
# Experience bank, NOT the Knowledge bank (which is product/policy facts only).
# No \b word boundaries: CJK has none; rely on substring match.
_HANDLING_EXP_RE = re.compile(
    r"(?i)apologize|apologise|soothe|de-escalat|calm\s+the\s+customer|安抚|急躁|情绪|致歉|"
    r"客户追问|连续追问|customer\s+emotion|response\s+strateg|handling\s+experience"
)


def _list_memories(*, limit: int, offset: int, type_filter: str | None, state: str | None) -> dict[str, Any]:
    import httpx
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if type_filter:
        params["type"] = type_filter
    if state:
        params["state"] = state
    url = f"{EXP_BASE_URL}{API_PREFIX}/banks/{EXP_BANK}/memories/list"
    with httpx.Client(timeout=LIST_TIMEOUT, headers=ko._headers()) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json()


def _is_reusable(item: dict[str, Any]) -> tuple[bool, str]:
    """Return (reusable, skip_reason). Mirrors plan P2b reuse criteria.

    Content-based: a fact may carry a `session:*` origin tag but still be reusable product
    knowledge. We skip agent-operational narratives, one-off compensation, and PII-residual text."""
    text = item.get("text") or ""
    if _AGENT_NARRATIVE_RE.search(text):
        return False, "agent_narrative"
    if _HANDLING_EXP_RE.search(text):
        return False, "handling_experience"  # belongs in Experience bank, not Knowledge bank
    if _COMPENSATION_RE.search(text):
        return False, "one_off_compensation"
    # residual PII after redaction -> skip (do not migrate dirty text)
    redacted, fields = ko.heuristic_redact(text)
    if ko.scan_pii(redacted):
        return False, "residual_pii"
    if not text.strip():
        return False, "empty"
    return True, ""


def _infer_domain(text: str, entities: list[Any], tags: list[Any] | None = None) -> tuple[str, str]:
    """Infer (domain, product_name) from text + entities + source tags (old scheme:
    `product_name:*` / `product_material:*`) for re-classification."""
    q = (text or "").lower()
    ent_str = " ".join(str(e) for e in entities or []).lower()
    tags = tags or []
    product_name = ""
    for t in tags:
        ts = str(t)
        if ts.startswith("product_name:"):
            product_name = ts.split(":", 1)[1].strip()
    has_product = bool(product_name) or bool(re.search(r"\b[A-Z0-9]{2,}-[A-Z0-9]{3,}\b", text or "")) \
        or "product:" in ent_str or any(str(t).startswith(("product_name:", "product_material:")) for t in tags)
    ptype = next((pt for pt in ko.POLICY_TYPES_EXTENDED if pt in q or pt in ent_str), None)
    if has_product and ptype:
        return ko.DOMAIN_BOTH, product_name
    if ptype:
        return ko.DOMAIN_POLICY, product_name
    if has_product:
        return ko.DOMAIN_PRODUCT, product_name
    # default: treat as product-ish world fact only if it mentions specs/materials
    if any(k in q for k in ("spec", "material", "dimension", "composition", "certif", "warranty", "kg", "inch", "polyester", "velvet", "linen")):
        return ko.DOMAIN_PRODUCT, product_name
    return ko.DOMAIN_POLICY, product_name  # ambiguous → policy (safer; won't pollute product recall)


def migrate_one(item: dict[str, Any], *, dry_run: bool, source_tag: str = "user_reported") -> dict[str, Any]:
    """Refine + retain a single Experience-bank fact into the Knowledge bank. Add-only."""
    text = (item.get("text") or "").strip()
    entities = item.get("entities") or []
    tags = item.get("tags") or []
    reusable, reason = _is_reusable(item)
    if not reusable:
        return {"id": item.get("id"), "status": "skipped", "reason": reason}

    redacted, _fields = ko.heuristic_redact(text)
    domain, product_name = _infer_domain(text, entities, tags)
    # extract SKU from free text — supports hyphenated (M2-SF8248) and non-hyphenated (DT8366DD150) forms
    sku_match = re.search(r"\b([A-Z]{1,4}\d{3,}[A-Z0-9]*|[A-Z0-9]{2,}-[A-Z0-9]{3,})\b", text or "")
    product_id = sku_match.group(1) if (sku_match and domain in (ko.DOMAIN_PRODUCT, ko.DOMAIN_BOTH)) else ""
    # Build a KnowledgeObject treating the fact text as both question hint and answer.
    ko_obj = ko.KnowledgeObject(
        domain=domain,
        product_id=product_id,
        product_name=product_name,
        attribute="",
        policy_type=next((pt for pt in ko.POLICY_TYPES_EXTENDED if pt in (text or "").lower()), "") if domain in (ko.DOMAIN_POLICY, ko.DOMAIN_BOTH) else "",
        applies_to="all",
        version="",
        effective_from="",
        effective_to="",
        category="migrated",
        source=source_tag,
        confirmed="false",
        evidence_doc=f"migrated_from:{item.get('id', '')}",
        aliases=[],
        question=text[:200],
        answer=redacted,
        key_fact=redacted,
        evidence=[f"migrated_from:{item.get('id', '')}"],
        confidence=0.5,
        reusable=True,
        skip_reason=None,
        redacted_fields=_fields,
        env=os.environ.get("CS_MIGRATE_ENV", "LIVE"),
        created_at="",
    )
    payload = ko.build_retain_payload(ko_obj)
    if dry_run:
        return {"id": item.get("id"), "status": "dry_run", "domain": domain, "redacted_fields": _fields,
                "preview": redacted[:80]}
    resp = ko.http_retain(payload)
    return {"id": item.get("id"), "status": "retained" if "error" not in resp else "error",
            "domain": domain, "http": resp.get("error") or resp.get("operation_id") or resp.get("success")}


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate Experience-bank facts → Knowledge bank (add-only).")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--type", default="", help="fact type filter (world|experience|observation)")
    ap.add_argument("--state", default="", help="consolidation state filter")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-tag", default="user_reported", help="source metadata value for migrated facts")
    args = ap.parse_args()

    print(f"Source: {EXP_BANK} @ {EXP_BASE_URL}")
    print(f"Target: {ko.KNOWLEDGE_BANK} @ {ko.HINDSIGHT_BASE_URL} (add-only, async)")
    print(f"Window: offset={args.offset} limit={args.limit} type={args.type or '(all)'} dry_run={args.dry_run}\n")

    page = _list_memories(limit=args.limit, offset=args.offset,
                          type_filter=args.type or None, state=args.state or None)
    items = page.get("items", [])
    total = page.get("total", 0)
    print(f"Window returned {len(items)} items (bank total ~{total}).\n")

    stats = {"retained": 0, "skipped": 0, "dry_run": 0, "error": 0}
    skip_reasons: dict[str, int] = {}
    for it in items:
        res = migrate_one(it, dry_run=args.dry_run, source_tag=args.source_tag)
        st = res["status"]
        stats[st] = stats.get(st, 0) + 1
        if st == "skipped":
            skip_reasons[res["reason"]] = skip_reasons.get(res["reason"], 0) + 1
        line = f"  [{st:7s}] {res.get('id', '')[:8]} "
        if st == "skipped":
            line += f"reason={res['reason']}"
        elif st == "dry_run":
            line += f"domain={res['domain']} preview={res['preview']!r}"
        elif st == "retained":
            line += f"domain={res['domain']}"
        elif st == "error":
            line += f"http={res.get('http')}"
        print(line)

    print("\n=== summary ===")
    print(json.dumps({"stats": stats, "skip_reasons": skip_reasons, "bank_total": total}, ensure_ascii=False, indent=2))
    if not args.dry_run and stats.get("retained", 0) > 0:
        print("\nNext: poll pending_consolidation on the Knowledge bank until stable, then run")
        print("observation-only recall to verify migrated facts are retrievable.")


if __name__ == "__main__":
    main()
