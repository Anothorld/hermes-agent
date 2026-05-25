"""Povison product API client and response shaping."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

import requests

from plugins.povison_product.internal.errors import PovisonAPIError, PovisonConfigError
from plugins.povison_product.schemas import DEFAULT_STORE_ID, ParsedProductLink

logger = logging.getLogger(__name__)

POVISON_PRODUCT_ENDPOINT = "https://www.povison.com/api/product-server/openApi/product/modelProductList"
POVISON_PRODUCT_BASE_URL = "https://www.povison.com/"


class PovisonProductClient:
    """Small client for Povison's model product list API."""

    def __init__(
        self,
        *,
        client_token: str | None = None,
        session: requests.Session | None = None,
        timeout_seconds: float = 15.0,
        endpoint: str = POVISON_PRODUCT_ENDPOINT,
    ) -> None:
        self._client_token = (client_token or os.environ.get("POVISON_CLIENT_TOKEN") or "").strip()
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds
        self._endpoint = endpoint

    def fetch_model_product_list(self, *, path: str, variant: str, store_id: int = DEFAULT_STORE_ID) -> dict[str, Any]:
        """Fetch Povison model product data.

        Args:
            path: Product path without a leading slash.
            variant: Povison SKU/entity id from the product URL.
            store_id: Store id for both the JSON body and `storeId` header.

        Returns:
            Parsed JSON response from the API.

        Raises:
            PovisonConfigError: If `POVISON_CLIENT_TOKEN` is missing.
            PovisonAPIError: If HTTP or API-level status indicates failure.
        """
        if not self._client_token:
            raise PovisonConfigError("POVISON_CLIENT_TOKEN is required")

        payload = {"storeId": store_id, "path": path, "variant": variant}
        logger.info("Fetching Povison product data for path=%s variant=%s", path, variant)
        response = self._session.post(
            self._endpoint,
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Clienttoken": self._client_token,
                "storeId": str(store_id),
            },
            json=payload,
            timeout=self._timeout_seconds,
        )

        try:
            body = response.json()
        except ValueError as exc:
            raise PovisonAPIError("Povison API returned non-JSON response", status_code=response.status_code) from exc

        if response.status_code >= 400:
            raise PovisonAPIError(
                str(body.get("msg") or f"Povison API HTTP {response.status_code}"),
                status_code=response.status_code,
                api_code=_optional_int(body.get("code")),
            )

        if body.get("code") != 200:
            raise PovisonAPIError(
                str(body.get("msg") or "Povison API request failed"),
                status_code=response.status_code,
                api_code=_optional_int(body.get("code")),
            )

        return body


def build_lookup_result(parsed: ParsedProductLink, payload: Mapping[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Build a compact product lookup result from the API payload.

    Args:
        parsed: Parsed product link fields.
        payload: Raw Povison API response.
        include_raw: Include the full API response when true.

    Returns:
        Stable summary containing product, selected SKU, options, and variants.
    """
    info = payload.get("info") if isinstance(payload, Mapping) else None
    details = info.get("productDetailVOList") if isinstance(info, Mapping) else None
    detail = details[0] if isinstance(details, list) and details and isinstance(details[0], Mapping) else {}
    spu_info = detail.get("spuInfo") if isinstance(detail, Mapping) else {}
    sku_list = detail.get("skuList") if isinstance(detail, Mapping) else []
    variants = [_summarize_sku(sku) for sku in sku_list if isinstance(sku, Mapping)]
    selected = next((variant for variant in variants if str(variant.get("variant")) == parsed.variant), None)

    result: dict[str, Any] = {
        "success": True,
        "request": {
            "url": parsed.original_url,
            "path": parsed.path,
            "variant": parsed.variant,
            "storeId": parsed.store_id,
        },
        "product": {
            "spu": _mapping_get(spu_info, "spu"),
            "spu_id": _mapping_get(spu_info, "spuId"),
            "entity_id": _mapping_get(spu_info, "entityId"),
            "name": _mapping_get(spu_info, "name"),
            "review_count": _mapping_get(spu_info, "reviewCount"),
            "top_rated": _mapping_get(spu_info, "topRated"),
        },
        "selected_sku": selected,
        "selected_variant_found": selected is not None,
        "option_groups": _summarize_option_groups(detail.get("saleAttr") if isinstance(detail, Mapping) else []),
        "variants": variants,
    }
    if include_raw:
        result["raw"] = payload
    return result


def _summarize_sku(sku: Mapping[str, Any]) -> dict[str, Any]:
    detail_url = sku.get("detailUrl")
    return {
        "variant": sku.get("entityId"),
        "sku": sku.get("sku"),
        "name": sku.get("name"),
        "product_name": sku.get("productName"),
        "detail_url": _absolute_product_url(detail_url),
        "price": sku.get("price"),
        "special_price": sku.get("specialPrice"),
        "sale_price": sku.get("salePrice"),
        "salable_quantity": sku.get("salableQuantity"),
        "status": sku.get("status"),
        "options": _option_map(sku.get("saleValueList")),
        "dimensions": sku.get("dimensions"),
    }


def _summarize_option_groups(raw_groups: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_groups, list):
        return []
    groups = []
    for group in raw_groups:
        if not isinstance(group, Mapping):
            continue
        values = group.get("valueList") if isinstance(group.get("valueList"), list) else []
        groups.append({
            "code": group.get("attributeCode"),
            "label": group.get("attributeLabel") or group.get("translateName"),
            "values": [
                {
                    "value_id": value.get("valueId"),
                    "value": value.get("value"),
                    "en_value": value.get("enValue"),
                    "swatch_value": value.get("swatchValue"),
                }
                for value in values
                if isinstance(value, Mapping)
            ],
        })
    return groups


def _option_map(raw_values: Any) -> dict[str, Any]:
    if not isinstance(raw_values, list):
        return {}
    options: dict[str, Any] = {}
    for value in raw_values:
        if not isinstance(value, Mapping):
            continue
        key = str(value.get("attributeCode") or value.get("attributeLabel") or "").strip().lower()
        if key:
            options[key] = value.get("enValue") or value.get("value") or value.get("swatchValue")
    return options


def _absolute_product_url(detail_url: Any) -> str | None:
    if not isinstance(detail_url, str) or not detail_url.strip():
        return None
    cleaned = detail_url.strip()
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return POVISON_PRODUCT_BASE_URL + cleaned.lstrip("/")


def _mapping_get(raw: Any, key: str) -> Any:
    return raw.get(key) if isinstance(raw, Mapping) else None


def _optional_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
