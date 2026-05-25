"""Povison product lookup plugin."""

from __future__ import annotations

from plugins.povison_product.tools import (
    POVISON_PRODUCT_INFO_SCHEMA,
    _check_povison_product_available,
    _handle_povison_product_info,
)

__all__ = ["register"]


def register(ctx) -> None:
    """Register the Povison product lookup tool."""
    ctx.register_tool(
        name="povison_product_info",
        toolset="povison_product",
        schema=POVISON_PRODUCT_INFO_SCHEMA,
        handler=_handle_povison_product_info,
        check_fn=_check_povison_product_available,
        emoji="P",
    )
