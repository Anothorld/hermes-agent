"""Tool handler + schema for serp_fetch_google.

Imports of the hermes core (``tools.registry``) and the ``internal/`` modules are
done lazily inside the handlers so this module imports cleanly in isolation.

IMPORTANT: only ``internal/`` is added to ``sys.path`` — NOT the plugin dir itself.
Adding the plugin dir would shadow the hermes core ``tools`` package (because this
file is named ``tools.py``), breaking ``from tools.registry import ...`` at runtime.
The schema is inlined here (not imported from a sibling ``schemas.py``) for the same
reason. This mirrors the ``kol-discovery-rpa`` plugin pattern.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Make internal/ importable via absolute imports (cache/providers/client). Only
# internal/ — never the plugin dir (see module docstring re: the `tools` collision).
_PLUGIN_DIR = Path(__file__).resolve().parent
_INTERNAL_DIR = str(_PLUGIN_DIR / "internal")
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

# Schema is inlined (not imported from schemas.py) to avoid putting the plugin dir
# on sys.path, which would shadow the hermes core `tools` package.
SERP_FETCH_GOOGLE_SCHEMA: dict[str, Any] = {
    "name": "serp_fetch_google",
    "description": (
        "Fetch Google organic SERP results (title, url, snippet, rank) for a "
        "query via a SERP API provider (Google Custom Search / Serper / SerpAPI / "
        "Valueserp). No browser/CDP — works on datacenter IPs that get CAPTCHA'd by "
        "direct Google scraping. Results are file-cached (24h default) to save quota. "
        "Returns the same shape as rpa_fetch_google_serp: data.results[] with "
        "rank/title/url/snippet. Use this for SEO brainstorm gap analysis instead "
        "of browser-based SERP scraping."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (will be URL-encoded by the provider).",
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "maximum": 40,
                "description": (
                    "Organic result rows to return (default 10, max 40). Google CSE "
                    "caps at 10 per request; other providers paginate to reach more."
                ),
            },
            "gl": {
                "type": "string",
                "default": "us",
                "description": "Google geo parameter, e.g. 'us', 'uk'. Default 'us'.",
            },
            "hl": {
                "type": "string",
                "default": "en",
                "description": "Google language parameter, e.g. 'en', 'zh-CN'. Default 'en'.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _check_serp_api_available() -> bool:
    """Return True unless SERP_API_ENABLED=0 (master kill switch)."""
    if os.environ.get("SERP_API_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    from providers import is_configured  # noqa: E402 (absolute, internal/ on path)
    return is_configured()


def _cache_ttl() -> int:
    try:
        return int(os.environ.get("SERP_API_CACHE_TTL", "86400"))
    except ValueError:
        return 86400


def _handle_fetch_google(args: dict[str, Any], **_: Any) -> str:
    """Handle serp_fetch_google — fetch Google organic SERP via a SERP API."""
    from tools.registry import tool_error, tool_result  # noqa: E402 (lazy: hermes core)

    query = str(args.get("query", "")).strip()
    if not query:
        return tool_error("query is required", code="missing_arg")

    max_results = min(int(args.get("max_results", 10)), 40)
    gl = str(args.get("gl", "us")).strip() or "us"
    hl = str(args.get("hl", "en")).strip() or "en"

    from cache import cache_get, cache_put  # noqa: E402 (absolute, internal/ on path)
    from providers import fetch, is_configured, resolve_provider  # noqa: E402

    provider = resolve_provider()
    if not is_configured(provider):
        hint = (
            "set GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX (free, default)"
            if provider == "google_cse"
            else f"set SERP_API_KEY (or the provider-specific key) for provider '{provider}'"
        )
        return tool_error(
            f"serp-api provider '{provider}' is not configured: {hint}",
            code="not_configured",
        )

    ttl = _cache_ttl()
    cached = cache_get(provider, query, gl, hl, ttl)
    if cached is not None:
        return tool_result({
            "ok": True,
            "data": cached,
            "errors": [],
            "meta": {
                "elapsed_ms": 0,
                "provider": provider,
                "cached": True,
                "cache_ttl_s": ttl,
            },
        })

    import time
    started = time.time()
    try:
        data = fetch(query, max_results, gl, hl, provider)
    except Exception as exc:  # noqa: BLE001 (surface provider errors to the agent)
        return tool_error(str(exc), code="serp_api_error", provider=provider)

    elapsed_ms = int((time.time() - started) * 1000)
    cache_put(provider, query, gl, hl, data)

    return tool_result({
        "ok": True,
        "data": data,
        "errors": [],
        "meta": {
            "elapsed_ms": elapsed_ms,
            "provider": provider,
            "cached": False,
            "cache_ttl_s": ttl,
        },
    })
