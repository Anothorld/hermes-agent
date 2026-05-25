"""Best-effort product-URL → variant parser.

Many merchant URLs encode the chosen variant in the query string. Examples we
already see in operator briefs:

    https://www.povison.com/.../walnut.html?variant=32529
    https://shop.example/widget?variant=12345&color=red&size=L

The parser intentionally stays narrow: it only extracts what the URL itself
exposes. It does **not** fetch the page or attempt to enumerate every
attribute Shopify-style storefronts could carry — that would be a brittle
scraper and outside the scope of a deterministic console helper.

Returns a single-element variant list when a recognisable ``variant`` /
``sku`` / ``id`` token is present, otherwise an empty list (the operator
adds variants manually for stores that don't expose a stable token).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

_VARIANT_KEYS = ("variant", "variant_id", "variantid", "sku", "id")
_ATTR_KEYS = ("color", "colour", "size", "style", "finish", "material")


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
    label_bits: list[str] = [f"variant {variant_id}"]
    for k, v in attributes.items():
        label_bits.append(f"{k}={v}")
    return [
        {
            "id": variant_id,
            "label": " · ".join(label_bits),
            "url": url,
            "attributes": attributes,
        }
    ]
