"""URL parsing helpers for Povison product pages."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from plugins.povison_product.internal.errors import PovisonParseError
from plugins.povison_product.schemas import DEFAULT_STORE_ID, ParsedProductLink


def parse_product_link(url: str, *, store_id: int = DEFAULT_STORE_ID) -> ParsedProductLink:
    """Parse a Povison product URL into API request fields.

    Args:
        url: Full product URL, e.g. `https://www.povison.com/products/foo.html?variant=123`.
        store_id: Store id used by the Povison product API.

    Returns:
        Parsed path and variant values.

    Raises:
        PovisonParseError: If the URL has no path or no `variant` query parameter.
    """
    raw = str(url or "").strip()
    if not raw:
        raise PovisonParseError("url is required")

    parsed = urlparse(raw)
    path = unquote(parsed.path or "").lstrip("/")
    if not path:
        raise PovisonParseError("Product URL path is required")

    query = parse_qs(parsed.query, keep_blank_values=False)
    variants = [value.strip() for value in query.get("variant", []) if value.strip()]
    if not variants:
        raise PovisonParseError("Product URL must include a variant query parameter")

    return ParsedProductLink(
        original_url=raw,
        host=parsed.netloc or None,
        path=path,
        variant=variants[0],
        store_id=store_id,
    )
