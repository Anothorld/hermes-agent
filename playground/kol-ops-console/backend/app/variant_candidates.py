"""Canonical product variant candidate helpers for campaign + email flows.

Each candidate is the unit KOL product-selection emails propose. Internal
fields (``id``, ``merchant_sku``) must never appear in KOL-facing copy —
only ``product_display_name`` + human-readable spec text + ``url``.
"""

from __future__ import annotations

import json
import re
from typing import Any

_ATTR_ORDER = ("size", "material", "color", "sale_category")

# Patterns that must not appear in outbound email bodies.
_FORBIDDEN_EMAIL_PATTERNS = (
    re.compile(r"\bvariant\s+\d+\b", re.I),
    re.compile(r"\bSKU\s*[:#]?\s*[A-Z0-9\-]+\b", re.I),
    re.compile(r"\bSF\d{4}[A-Z0-9]+\b"),
)


def normalize_variant(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical variant candidate dict."""
    vid = str(raw.get("id") or "").strip()
    if not vid:
        raise ValueError("variant candidate requires non-empty id")
    attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    clean_attrs = {
        str(k).strip().lower().replace(" ", "_"): str(v).strip()
        for k, v in attributes.items()
        if str(k).strip() and str(v).strip()
    }
    label = str(raw.get("label") or "").strip() or human_spec_text(clean_attrs)
    out: dict[str, Any] = {
        "id": vid,
        "label": label,
        "url": (str(raw.get("url")).strip() if raw.get("url") else None),
        "attributes": clean_attrs,
    }
    merchant_sku = raw.get("merchant_sku") or raw.get("sku")
    if merchant_sku is not None and str(merchant_sku).strip():
        out["merchant_sku"] = str(merchant_sku).strip()
    for price_key in ("price", "discounted_price", "sale_price"):
        val = raw.get(price_key)
        if val is not None and val != "":
            try:
                out[price_key] = float(val)
            except (TypeError, ValueError):
                pass
    if raw.get("price_updated_at"):
        out["price_updated_at"] = str(raw["price_updated_at"])
    return out


def human_spec_text(attributes: dict[str, str]) -> str:
    """Build a human-readable spec line from attribute map."""
    bits: list[str] = []
    seen: set[str] = set()
    for key in _ATTR_ORDER:
        val = attributes.get(key)
        if val and val not in seen:
            bits.append(val)
            seen.add(val)
    for key, val in attributes.items():
        if key in _ATTR_ORDER or not val or val in seen:
            continue
        bits.append(val)
        seen.add(val)
    return " / ".join(bits)


def email_option_line(
    *,
    product_display_name: str,
    variant: dict[str, Any],
) -> str:
    """One KOL-visible option line: product name + spec (+ optional link hint)."""
    spec = human_spec_text(variant.get("attributes") or {}) or (
        str(variant.get("label") or "").strip()
    )
    name = product_display_name.strip()
    if spec and spec.lower() != name.lower():
        line = f"{name} — {spec}"
    else:
        line = name
    url = variant.get("url")
    if url:
        line += f"\nView option: {url}"
    return line


def build_color_variant_policy(variants: list[dict[str, Any]]) -> str | None:
    """Summarize allowed specs for campaign_config.color_variant_policy."""
    if not variants:
        return None
    labels = [human_spec_text(v.get("attributes") or {}) or v.get("label") or v["id"]
              for v in variants]
    unique = list(dict.fromkeys(str(x) for x in labels if str(x).strip()))
    if not unique:
        return None
    return "operator_selected: " + " | ".join(unique)


def synthetic_variant_from_product(
    *,
    product_sku: str,
    product_name: str,
    product_url: str | None,
) -> dict[str, Any]:
    """Fallback when catalog has no parsed variants."""
    vid = str(product_url or product_sku).strip()
    return normalize_variant(
        {
            "id": vid,
            "label": product_name,
            "url": product_url,
            "attributes": {},
        }
    )


def resolve_campaign_variants(
    *,
    product_variants: list[dict[str, Any]],
    selected_ids: list[str] | None,
    product_sku: str,
    product_name: str,
    product_url: str | None,
) -> list[dict[str, Any]]:
    """Filter catalog variants to campaign scope; synthesize one if empty."""
    normalized = [normalize_variant(v) for v in product_variants if isinstance(v, dict)]
    if not normalized:
        return [synthetic_variant_from_product(
            product_sku=product_sku,
            product_name=product_name,
            product_url=product_url,
        )]
    if not selected_ids:
        return normalized
    wanted = {str(v) for v in selected_ids}
    picked = [v for v in normalized if v["id"] in wanted]
    return picked or normalized


def parse_variants_from_extra_notes(extra_notes: str | None) -> list[dict[str, Any]]:
    """Legacy fallback: read ``# product_variants`` JSON block from extra_notes."""
    if not extra_notes or "# product_variants" not in extra_notes:
        return []
    chunk = extra_notes.split("# product_variants", 1)[1].strip()
    if not chunk:
        return []
    first_line = chunk.split("\n", 1)[0].strip()
    try:
        data = json.loads(first_line)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            try:
                out.append(normalize_variant(item))
            except ValueError:
                continue
    return out


def variant_candidates_from_campaign_config(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Read variant candidates from dispatch-context campaign_config."""
    raw = cfg.get("variant_candidates")
    if isinstance(raw, list) and raw:
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    out.append(normalize_variant(item))
                except ValueError:
                    continue
        if out:
            return out
    return parse_variants_from_extra_notes(cfg.get("extra_notes"))


def assert_email_has_no_internal_ids(body: str) -> None:
    """Raise ValueError if draft body leaks internal ids (test/validation helper)."""
    for pat in _FORBIDDEN_EMAIL_PATTERNS:
        if pat.search(body or ""):
            raise ValueError(f"email body contains forbidden internal id pattern: {pat.pattern}")
