"""HTTP-layer helpers for the Veedcrawl client.

Kept separate from ``client.py`` so the public client stays under the
project's 250-line file budget. These helpers know nothing about caching,
polling, or credit guardrails — they only translate ``httpx.Response``
objects into typed errors / decoded JSON.
"""

from __future__ import annotations

from typing import Any

import httpx

from plugins.veedcrawl._internal import rate_limit
from plugins.veedcrawl._internal.errors import (
    VeedcrawlAPIError,
    VeedcrawlAuthRequiredError,
    VeedcrawlInsufficientCreditsError,
    VeedcrawlJobTimeoutError,
    VeedcrawlRateLimitError,
)


def decode_response(response: httpx.Response) -> dict[str, Any]:
    """Return the JSON payload (or a wrapped text body) from ``response``."""
    if response.status_code == 204 or not response.content:
        return {}
    ctype = response.headers.get("content-type", "")
    if "application/json" not in ctype:
        return {"_text": response.text}
    return response.json()


def raise_for_status(response: httpx.Response) -> None:
    """Map ``response`` (status >= 400) onto the typed error hierarchy."""
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text}

    message = str(
        body.get("message") or body.get("error") or response.text or "unknown error"
    )
    error_code = str(body.get("error") or "")

    if status == 401:
        raise VeedcrawlAuthRequiredError(message)
    if status == 403:
        # Documented as "insufficient credits or plan restriction".
        raise VeedcrawlInsufficientCreditsError(message, status_code=403)
    if status == 429:
        raise VeedcrawlRateLimitError(
            message, reset_at=rate_limit.parse_reset(response.headers)
        )
    if status == 504:
        # Documented as transcript/extract job timeout (~3 min).
        raise VeedcrawlJobTimeoutError(message, status_code=504)
    raise VeedcrawlAPIError(
        message or f"HTTP {status}",
        status_code=status,
        body=error_code or None,
    )
