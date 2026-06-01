"""Product URL -> variant parser.

Generic links still use best-effort query-string extraction only.

For Povison links we additionally call the documented product API
(``/api/product-server/openApi/product/modelProductList``) to enumerate all
SKU variants under the product path. This lets operators seed the full variant
catalog in one click when adding a product.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

_VARIANT_KEYS = ("variant", "variant_id", "variantid", "sku", "id")
_ATTR_KEYS = ("color", "colour", "size", "style", "finish", "material")
_POVISON_HOSTS = {"povison.com", "www.povison.com"}
_POVISON_MODEL_LIST_API = (
    "https://www.povison.com/api/product-server/openApi/product/modelProductList"
)
_POVISON_TIMEOUT_SEC = 10.0


def parse_variants_from_url(url: str) -> list[dict[str, Any]]:
    """Return a list of variant descriptors parsed from ``url``.

    Each entry has the shape::

        {"id": "<token>", "label": "<token + attr summary>",
         "url": "<url>", "attributes": {"color": "...", "size": "..."}}

    Empty list when no variant token is recognisable.
    """
    if not url or not isinstance(url, str):
        return []
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return []
    if not parsed.scheme or not parsed.netloc:
        return []
    if parsed.netloc.lower() in _POVISON_HOSTS:
        variants = _parse_povison_variants(parsed)
        if variants:
            return variants
    return _parse_variant_from_query(url, parsed)


def _parse_variant_from_query(url: str, parsed: Any) -> list[dict[str, Any]]:
    query = parse_qs(parsed.query, keep_blank_values=False)
    variant_id: str | None = None
    for key in _VARIANT_KEYS:
        values = query.get(key) or query.get(key.lower())
        if values and values[0].strip():
            variant_id = values[0].strip()
            break
    if not variant_id:
        return []
    attributes: dict[str, str] = {}
    for key in _ATTR_KEYS:
        values = query.get(key) or query.get(key.lower())
        if values and values[0].strip():
            attributes[key] = values[0].strip()
    label_bits: list[str] = []
    for k, v in attributes.items():
        label_bits.append(f"{k}={v}")
    label = " · ".join(label_bits) if label_bits else None
    return [
        {
            "id": variant_id,
            "label": label,
            "url": url,
            "attributes": attributes,
        }
    ]


def _parse_povison_variants(parsed: Any) -> list[dict[str, Any]]:
    path = parsed.path.lstrip("/")
    if not path:
        return []
    query = parse_qs(parsed.query, keep_blank_values=False)
    selected_variant = (query.get("variant") or [None])[0]
    sku_rows = _fetch_povison_sku_list(path=path, variant=selected_variant)
    if not sku_rows:
        return []
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in sku_rows:
        if not isinstance(row, dict):
            continue
        variant_id = _variant_id_from_row(row)
        if not variant_id or variant_id in seen_ids:
            continue
        seen_ids.add(variant_id)
        detail_url = str(row.get("detailUrl") or "").strip()
        variant_url = urljoin(base_url, detail_url) if detail_url else None
        attributes = _attributes_from_sale_values(row.get("saleValueList"))
        price_fields = _price_fields_from_row(row)
        variant: dict[str, Any] = {
            "id": variant_id,
            "label": _label_for_variant(row=row, attributes=attributes),
            "url": variant_url,
            "attributes": attributes,
            **price_fields,
        }
        merchant_sku = row.get("sku")
        if merchant_sku is not None and str(merchant_sku).strip():
            variant["merchant_sku"] = str(merchant_sku).strip()
        out.append(variant)
    return out


def _fetch_povison_sku_list(path: str, variant: str | None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"storeId": _povison_store_id(), "path": path}
    if variant:
        payload["variant"] = str(variant)
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "storeId": str(payload["storeId"]),
    }
    token = os.environ.get("KOC_POVISON_CLIENT_TOKEN", "").strip()
    if token:
        headers["Clienttoken"] = token
    try:
        resp = httpx.post(
            _POVISON_MODEL_LIST_API,
            json=payload,
            headers=headers,
            timeout=_POVISON_TIMEOUT_SEC,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return []
    detail_list = info.get("productDetailVOList")
    if not isinstance(detail_list, list):
        return []
    out: list[dict[str, Any]] = []
    for detail in detail_list:
        if not isinstance(detail, dict):
            continue
        sku_list = detail.get("skuList")
        if not isinstance(sku_list, list):
            continue
        for sku in sku_list:
            if isinstance(sku, dict):
                out.append(sku)
    return out


def _variant_id_from_row(row: dict[str, Any]) -> str | None:
    entity_id = row.get("entityId")
    if entity_id is not None and str(entity_id).strip():
        return str(entity_id).strip()
    detail_url = str(row.get("detailUrl") or "")
    if detail_url:
        parsed = urlparse(detail_url)
        query = parse_qs(parsed.query, keep_blank_values=False)
        values = query.get("variant")
        if values and values[0].strip():
            return values[0].strip()
    sku = row.get("sku")
    if sku is not None and str(sku).strip():
        return str(sku).strip()
    return None


def _attributes_from_sale_values(values: Any) -> dict[str, str]:
    if not isinstance(values, list):
        return {}
    attrs: dict[str, str] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        raw_key = (
            str(item.get("attributeCode") or item.get("attributeLabel") or "")
            .strip()
            .lower()
        )
        value = str(item.get("value") or "").strip()
        if not raw_key or not value:
            continue
        key = raw_key.replace(" ", "_")
        attrs[key] = value
    return attrs


def _price_fields_from_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src, dst in (
        ("price", "price"),
        ("discountedPrice", "discounted_price"),
        ("salePrice", "sale_price"),
    ):
        val = row.get(src)
        if val is None or val == "":
            continue
        try:
            out[dst] = float(val)
        except (TypeError, ValueError):
            continue
    if out:
        out["price_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


def _label_for_variant(row: dict[str, Any], attributes: dict[str, str]) -> str:
    ordered_values: list[str] = []
    for key in ("size", "material", "color"):
        v = attributes.get(key)
        if v:
            ordered_values.append(v)
    if ordered_values:
        return " / ".join(ordered_values)
    title = str(row.get("name") or row.get("productName") or "").strip()
    if title:
        return title
    return "Standard option"


def _povison_store_id() -> int:
    raw = os.environ.get("KOC_POVISON_STORE_ID", "").strip()
    if not raw:
        return 3
    try:
        value = int(raw)
    except ValueError:
        return 3
    return value if value > 0 else 3
