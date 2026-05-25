"""Hermes tool registration helpers for Povison product lookup."""

from __future__ import annotations

import os
from typing import Any

from pydantic import ValidationError

from plugins.povison_product.internal.client import PovisonProductClient, build_lookup_result
from plugins.povison_product.internal.errors import PovisonAPIError, PovisonConfigError, PovisonParseError
from plugins.povison_product.internal.parser import parse_product_link
from plugins.povison_product.schemas import ProductLookupRequest
from tools.registry import tool_error, tool_result


POVISON_PRODUCT_INFO_SCHEMA = {
    "name": "povison_product_info",
    "description": (
        "Fetch Povison product and variant/SKU information from a product page URL. "
        "The URL must include a variant query parameter. The tool extracts the API path "
        "and variant, then calls Povison's modelProductList API with storeId=3."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Povison product URL containing ?variant=..."},
            "include_raw": {"type": "boolean", "description": "Include full raw API payload. Defaults to false."},
            "timeout_seconds": {"type": "number", "description": "HTTP timeout, 1-60 seconds. Defaults to 15."},
        },
        "required": ["url"],
    },
}


def _check_povison_product_available() -> bool:
    """Return True when the Povison API client token is configured."""
    return bool(os.environ.get("POVISON_CLIENT_TOKEN", "").strip())


def _handle_povison_product_info(args: dict[str, Any], **_: Any) -> str:
    """Handle the `povison_product_info` tool call.

    Args:
        args: Tool arguments from Hermes.

    Returns:
        JSON tool result or structured tool error.
    """
    try:
        request = ProductLookupRequest.model_validate(args)
        parsed = parse_product_link(request.url)
        client = PovisonProductClient(timeout_seconds=request.timeout_seconds)
        payload = client.fetch_model_product_list(
            path=parsed.path,
            variant=parsed.variant,
            store_id=parsed.store_id,
        )
        return tool_result(build_lookup_result(parsed, payload, include_raw=request.include_raw))
    except PovisonConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except PovisonParseError as exc:
        return tool_error(str(exc), invalid_input=True)
    except PovisonAPIError as exc:
        return tool_error(str(exc), status_code=exc.status_code, api_code=exc.api_code)
    except ValidationError as exc:
        return tool_error(str(exc), invalid_input=True)
    except Exception as exc:  # noqa: BLE001 - preserve tool error envelope
        return tool_error(f"Povison product lookup failed: {type(exc).__name__}: {exc}")
