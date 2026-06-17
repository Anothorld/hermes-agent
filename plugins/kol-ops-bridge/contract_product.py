"""Deterministic contract product specs + link resolution."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Mapping

from . import deliverables_spec as ds
from . import product_variants as pv

_TOKEN_SPLIT = re.compile(r"[\s/·\-–—]+")
_PHONE_RE = re.compile(r"^[\d\s\-\+\(\)\.]+$")


def parse_variants_from_campaign_cfg(cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read variant rows from ``variant_candidates`` or ``# product_variants`` notes."""
    raw = cfg.get("variant_candidates")
    if isinstance(raw, list) and raw:
        return [v for v in raw if isinstance(v, dict)]
    extra = cfg.get("extra_notes")
    if isinstance(extra, str) and "# product_variants" in extra:
        chunk = extra.split("# product_variants", 1)[1].strip()
        first = chunk.split("\n", 1)[0].strip() if chunk else ""
        try:
            data = json.loads(first)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [v for v in data if isinstance(v, dict)]
    return []


def _variant_match_score(needle_l: str, variant: Mapping[str, Any]) -> int:
    """Score how well ``offer.color_or_variant_locked`` fits a catalog row."""
    label = str(variant.get("label") or "").lower()
    attrs = variant.get("attributes") if isinstance(variant.get("attributes"), dict) else {}
    hay = " ".join([label, *(str(v).lower() for v in attrs.values())]).strip()
    if not hay:
        return 0

    score = 0
    if needle_l in hay or hay in needle_l:
        score += 100
    tokens = [t for t in _TOKEN_SPLIT.split(needle_l) if len(t) > 2]
    if not tokens:
        return 0
    matched = sum(1 for tok in tokens if tok in hay)
    if matched < max(1, len(tokens) // 2):
        return 0
    score += matched * 10

    if "chenille" in needle_l and "chenille" in hay:
        score += 20

    size = str(attrs.get("size") or attrs.get("Size") or "").lower()
    if "2 seater" in needle_l and "2 seater" in size:
        score += 15
    elif "3 seater" in needle_l and "3 seater" in size:
        score += 15
    elif "4 seater" in needle_l and "4 seater" in size:
        score += 15
    elif not any(s in needle_l for s in ("2 seater", "3 seater", "4 seater")):
        if size == "3 seater":
            score += 8
    return score


def match_locked_variant(
    variants: list[dict[str, Any]],
    locked: str | None,
) -> dict[str, Any] | None:
    """Match ``offer.color_or_variant_locked`` to a catalog variant row."""
    if not locked or not variants:
        return None
    needle = str(locked).strip()
    if not needle:
        return None
    needle_l = needle.lower()

    for variant in variants:
        if str(variant.get("id") or "").strip() == needle:
            return variant
    for variant in variants:
        label = str(variant.get("label") or "").strip()
        if label and label.lower() == needle_l:
            return variant

    best: dict[str, Any] | None = None
    best_score = 0
    for variant in variants:
        score = _variant_match_score(needle_l, variant)
        if score > best_score:
            best_score = score
            best = variant
    if best is not None:
        return best
    return None


def _display_color_from_attrs(attrs: Mapping[str, Any], variant_locked: str | None) -> str:
    """Sales copy: ``Light Brown Chenille Fabric`` from color + material attrs."""
    color = str(
        attrs.get("color")
        or attrs.get("Color")
        or variant_locked
        or ""
    ).strip()
    material = str(attrs.get("material") or attrs.get("Material") or "").strip()
    if color and material and material.lower() not in color.lower():
        return f"{color} {material}"
    return color


def _variant_merchant_sku(variant: Mapping[str, Any] | None) -> str:
    if not variant:
        return ""
    return str(
        variant.get("merchant_sku") or variant.get("sku") or ""
    ).strip()


def _merge_variant_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge variant lists by ``id``; API rows enrich sparse campaign rows."""
    by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group:
            if not isinstance(row, dict):
                continue
            vid = str(row.get("id") or "").strip()
            if not vid:
                continue
            if vid not in by_id:
                by_id[vid] = dict(row)
                continue
            existing = by_id[vid]
            for key in ("merchant_sku", "sku", "url", "label"):
                if not existing.get(key) and row.get(key):
                    existing[key] = row[key]
            new_attrs = row.get("attributes")
            if isinstance(new_attrs, dict):
                merged_attrs = dict(existing.get("attributes") or {})
                merged_attrs.update(new_attrs)
                existing["attributes"] = merged_attrs
    return list(by_id.values())


def load_variant_catalog(
    campaign_cfg: Mapping[str, Any],
    *,
    fetch_live: bool = True,
) -> list[dict[str, Any]]:
    """Campaign-stored variants merged with live Povison parse when available."""
    local = parse_variants_from_campaign_cfg(campaign_cfg)
    api_rows: list[dict[str, Any]] = []
    product_url = campaign_cfg.get("product_url")
    if fetch_live and isinstance(product_url, str) and product_url.strip():
        try:
            api_rows = pv.parse_variants_from_url(product_url.strip())
        except Exception:  # noqa: BLE001 — network/API must not break render
            api_rows = []
    return _merge_variant_rows(local, api_rows)


def parse_fulfillment_address(blob: str | None) -> dict[str, str]:
    """Parse ``fulfillment.shipping_address`` into name, phone, and street lines."""
    if not blob or not str(blob).strip():
        return {}
    parts = [p.strip() for p in str(blob).split(",") if p.strip()]
    if not parts:
        return {}

    phone = ""
    if len(parts) > 1 and _PHONE_RE.match(parts[-1]):
        digits = re.sub(r"\D", "", parts[-1])
        if len(digits) >= 7:
            phone = parts.pop()

    full_name = parts[0] if parts else ""
    address = ", ".join(parts[1:]) if len(parts) > 1 else ""
    out: dict[str, str] = {}
    if full_name:
        out["full_name"] = full_name
    if phone:
        out["phone"] = phone
    if address:
        out["address"] = address
    return out


def normalize_social_url(
    value: str | None,
    *,
    platform: str,
    handle: str | None = None,
) -> str:
    """Return a full profile URL; use ``/`` when the platform is unused."""
    raw = str(value or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    h = (raw or handle or "").strip().lstrip("@")
    if not h:
        return "/"
    if platform == "instagram":
        return f"https://www.instagram.com/{h}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{h}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{h}"
    return raw or "/"


def _contract_date_parts(date_val: Any) -> tuple[str, str, str]:
    """Return ISO, long (intro), and short (signature) date strings."""
    if isinstance(date_val, _dt.date):
        d = date_val
    elif isinstance(date_val, _dt.datetime):
        d = date_val.date()
    elif isinstance(date_val, str) and date_val.strip():
        try:
            d = _dt.date.fromisoformat(date_val.strip()[:10])
        except ValueError:
            d = _dt.date.today()
    else:
        d = _dt.date.today()
    iso = d.isoformat()
    long_fmt = f"{d.strftime('%B')} {d.day}, {d.year}"
    short_fmt = f"{d.month}/{d.day}/{d.year}"
    return iso, long_fmt, short_fmt


def build_product_specs(
    *,
    sku_locked: str | None,
    variant_locked: str | None,
    campaign_cfg: Mapping[str, Any],
    matched_variant: Mapping[str, Any] | None = None,
) -> str | None:
    """Sales-style PRODUCT_SPECS line for the contract template."""
    name = str(
        campaign_cfg.get("label")
        or campaign_cfg.get("product_display_name")
        or ""
    ).strip()
    if name.lower().startswith("the "):
        name = name[4:].strip()
    if not name:
        return None

    attrs = {}
    if matched_variant and isinstance(matched_variant.get("attributes"), dict):
        attrs = matched_variant["attributes"]

    color = _display_color_from_attrs(attrs, variant_locked)
    size = str(attrs.get("size") or attrs.get("Size") or "3 Seater").strip()
    sku = _variant_merchant_sku(matched_variant) or str(
        attrs.get("sku") or attrs.get("SKU") or sku_locked or ""
    ).strip()

    parts: list[str] = []
    if color:
        parts.append(f"Color: {color}")
    if size:
        parts.append(f"Size: {size}")
    if sku:
        parts.append(f"SKU: {sku}")
    if not parts:
        return name
    return f"{name} ({'/ '.join(parts)})"


def resolve_product_link(
    *,
    variant_locked: str | None,
    campaign_cfg: Mapping[str, Any],
    fetch_live: bool = True,
) -> tuple[str | None, dict[str, Any] | None]:
    """Pick PRODUCT_LINK from locked variant; return (url, matched_variant)."""
    variants = load_variant_catalog(campaign_cfg, fetch_live=fetch_live)
    matched = match_locked_variant(variants, variant_locked)
    if matched:
        url = matched.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip(), matched
    product_url = campaign_cfg.get("product_url")
    if isinstance(product_url, str) and product_url.strip():
        return product_url.strip(), matched
    for variant in variants:
        url = variant.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip(), matched
    return None, matched


def enrich_contract_fields(
    fields: Mapping[str, Any],
    *,
    facts: Mapping[str, Any],
    campaign_cfg: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay deterministic influencer/product/deliverable fields onto agent JSON."""
    out = dict(fields)
    influencer = dict(out.get("influencer") or {})
    parsed = parse_fulfillment_address(
        str(facts.get("fulfillment.shipping_address") or ""),
    )
    handle = str(
        facts.get("identity.primary_handle")
        or facts.get("identity.display_name", "").split("|")[0].strip()
        or ""
    ).strip()

    if parsed.get("full_name"):
        influencer["full_name"] = parsed["full_name"]
    if parsed.get("phone"):
        influencer["phone"] = parsed["phone"]
    if parsed.get("address"):
        addr = parsed["address"]
        region = str(facts.get("identity.region") or "").strip()
        if region and region.lower() not in addr.lower():
            addr = f"{addr}, {region}"
        influencer["address"] = addr

    influencer["instagram"] = normalize_social_url(
        influencer.get("instagram"),
        platform="instagram",
        handle=handle,
    )
    influencer["tiktok"] = normalize_social_url(
        influencer.get("tiktok"),
        platform="tiktok",
        handle=handle,
    )
    yt = str(influencer.get("youtube") or "").strip()
    influencer["youtube"] = normalize_social_url(yt, platform="youtube") if yt else "/"
    out["influencer"] = influencer

    iso, long_fmt, short_fmt = _contract_date_parts(out.get("date"))
    out["date"] = iso
    out["date_long"] = long_fmt
    out["date_short"] = short_fmt

    product = dict(out.get("product") or {})
    variant_locked = facts.get("offer.color_or_variant_locked")
    sku_locked = facts.get("offer.sku_locked")
    resolved_link, matched = resolve_product_link(
        variant_locked=str(variant_locked) if variant_locked is not None else None,
        campaign_cfg=campaign_cfg,
    )
    resolved_specs = build_product_specs(
        sku_locked=str(sku_locked) if sku_locked is not None else None,
        variant_locked=str(variant_locked) if variant_locked is not None else None,
        campaign_cfg=campaign_cfg,
        matched_variant=matched,
    )
    agent_link = str(product.get("link") or "").strip()
    if resolved_link and (not agent_link or agent_link != resolved_link):
        product["link"] = resolved_link
    if resolved_specs:
        product["specs"] = resolved_specs
    out["product"] = product

    if campaign_cfg.get("campaign_deliverables_json"):
        out["deliverables"] = ds.build_contract_deliverables(campaign_cfg)
    else:
        out.pop("deliverables", None)

    return out


__all__ = [
    "build_product_specs",
    "enrich_contract_fields",
    "load_variant_catalog",
    "match_locked_variant",
    "normalize_social_url",
    "parse_fulfillment_address",
    "parse_variants_from_campaign_cfg",
    "resolve_product_link",
]
