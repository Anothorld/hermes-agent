"""SERP provider adapters.

Each adapter calls one SERP API and normalizes the response to a common shape::

    {
        "query": "<original query>",
        "results": [{"rank": int, "title": str, "url": str, "snippet": str}],
        "count": int,
        "provider": "<provider name>",
    }

Provider selection: ``SERP_API_PROVIDER`` env (default ``google_cse``).
API keys: a generic ``SERP_API_KEY`` plus provider-specific overrides
(``GOOGLE_CSE_API_KEY``/``GOOGLE_CSE_CX``, ``SERPER_API_KEY``, ``SERPAPI_KEY``,
``VALUESERP_KEY``). See README.md.
"""

from __future__ import annotations

import os
from typing import Any

from client import http_get_json, http_post_json

# Each provider's effective API key (provider-specific wins over generic SERP_API_KEY).
# wigolo needs no key by default (loopback is open); set WIGOLO_API_TOKEN only if the
# daemon is bound off-loopback with auth enabled.
PROVIDERS = ("google_cse", "serper", "serpapi", "valueserp", "wigolo", "brave")


def _key(provider: str) -> str:
    """Resolve the API key for a provider (provider-specific env wins)."""
    specific = {
        "google_cse": ("GOOGLE_CSE_API_KEY", "SERP_API_KEY"),
        "serper": ("SERPER_API_KEY", "SERP_API_KEY"),
        "serpapi": ("SERPAPI_KEY", "SERP_API_KEY"),
        "valueserp": ("VALUESERP_KEY", "SERP_API_KEY"),
        "wigolo": ("WIGOLO_API_TOKEN",),
        "brave": ("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY", "SERP_API_KEY"),
    }[provider]
    for env_name in specific:
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    return ""


def resolve_provider() -> str:
    """Return the configured provider (default google_cse)."""
    p = os.environ.get("SERP_API_PROVIDER", "google_cse").strip().lower()
    return p if p in PROVIDERS else "google_cse"


def is_configured(provider: str | None = None) -> bool:
    """True when the provider has the credentials it needs."""
    p = provider or resolve_provider()
    if p == "google_cse":
        return bool(_key(p)) and bool(os.environ.get("GOOGLE_CSE_CX", "").strip())
    if p == "wigolo":
        # wigolo needs no key (loopback open by default); only the URL is required.
        return bool(os.environ.get("WIGOLO_API_URL", "http://127.0.0.1:3333").strip())
    return bool(_key(p))


# --------------------------------------------------------------------- google_cse


def _google_cse(query: str, max_results: int, gl: str, hl: str) -> dict[str, Any]:
    """Google Custom Search (Programmable Search Engine) — free tier (100/day)."""
    key = _key("google_cse")
    cx = os.environ.get("GOOGLE_CSE_CX", "").strip()
    params = {
        "key": key,
        "cx": cx,
        "q": query,
        "num": min(max_results, 10),  # CSE caps at 10 per request
        "gl": gl,
        "hl": hl,
    }
    data = http_get_json(
        "https://www.googleapis.com/customsearch/v1", params, provider="google_cse"
    )
    items = data.get("items") or []
    results = [
        {
            "rank": i + 1,
            "title": (it.get("title") or "")[:200],
            "url": it.get("link") or "",
            "snippet": (it.get("snippet") or "")[:300],
        }
        for i, it in enumerate(items)
    ]
    return {"query": query, "results": results, "count": len(results), "provider": "google_cse"}


# ------------------------------------------------------------------------- serper


def _serper(query: str, max_results: int, gl: str, hl: str) -> dict[str, Any]:
    """Serper.dev — POST https://google.serper.dev/search."""
    headers = {"X-API-KEY": _key("serper"), "Content-Type": "application/json"}
    body = {"q": query, "num": min(max_results, 40), "gl": gl, "hl": hl}
    data = http_post_json(
        "https://google.serper.dev/search", body, headers, provider="serper"
    )
    organic = data.get("organic") or []
    results = [
        {
            "rank": i + 1,
            "title": (it.get("title") or "")[:200],
            "url": it.get("link") or "",
            "snippet": (it.get("snippet") or "")[:300],
        }
        for i, it in enumerate(organic)
    ]
    return {"query": query, "results": results, "count": len(results), "provider": "serper"}


# ------------------------------------------------------------------------ serpapi


def _serpapi(query: str, max_results: int, gl: str, hl: str) -> dict[str, Any]:
    """SerpAPI — GET https://serpapi.com/search (engine=google)."""
    params = {
        "engine": "google",
        "q": query,
        "num": min(max_results, 40),
        "gl": gl,
        "hl": hl,
        "api_key": _key("serpapi"),
    }
    data = http_get_json("https://serpapi.com/search", params, provider="serpapi")
    organic = data.get("organic_results") or []
    results = [
        {
            "rank": i + 1,
            "title": (it.get("title") or "")[:200],
            "url": it.get("link") or "",
            "snippet": (it.get("snippet") or "")[:300],
        }
        for i, it in enumerate(organic)
    ]
    return {"query": query, "results": results, "count": len(results), "provider": "serpapi"}


# ---------------------------------------------------------------------- valueserp


def _valueserp(query: str, max_results: int, gl: str, hl: str) -> dict[str, Any]:
    """Valueserp — GET https://api.valueserp.com/search."""
    params = {
        "api_key": _key("valueserp"),
        "q": query,
        "num": min(max_results, 40),
        "gl": gl,
        "hl": hl,
    }
    data = http_get_json("https://api.valueserp.com/search", params, provider="valueserp")
    organic = data.get("organic_results") or []
    results = [
        {
            "rank": i + 1,
            "title": (it.get("title") or "")[:200],
            "url": it.get("link") or "",
            "snippet": (it.get("snippet") or "")[:300],
        }
        for i, it in enumerate(organic)
    ]
    return {"query": query, "results": results, "count": len(results), "provider": "valueserp"}


# ------------------------------------------------------------------------- wigolo


def _wigolo_url() -> str:
    return os.environ.get("WIGOLO_API_URL", "http://127.0.0.1:3333").strip().rstrip("/")


def _wigolo(query: str, max_results: int, gl: str, hl: str) -> dict[str, Any]:
    """wigolo — local-first self-hosted search daemon (no API key needed).

    POST {WIGOLO_API_URL}/v1/search with {query, max_results, search_depth, country,
    language}. Returns {results:[{title,url,snippet,relevance_score,...}], engines_used}.
    Normalized to the common {rank,title,url,snippet} shape. search_depth="fast"
    (engines only, ≤1s, no content fetch) keeps brainstorm SERP queries cheap.
    """
    body = {
        "query": query,
        "max_results": min(max_results, 20),  # wigolo caps search at 20
        "search_depth": "fast",
        "country": gl,
        "language": hl,
    }
    headers = {"Content-Type": "application/json"}
    token = _key("wigolo")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = http_post_json(
        f"{_wigolo_url()}/v1/search", body, headers, provider="wigolo"
    )
    items = data.get("results") or []
    results = [
        {
            "rank": i + 1,
            "title": (it.get("title") or "")[:200],
            "url": it.get("url") or "",
            "snippet": (it.get("snippet") or "")[:300],
        }
        for i, it in enumerate(items)
    ]
    return {
        "query": query,
        "results": results,
        "count": len(results),
        "provider": "wigolo",
        "engines_used": data.get("engines_used") or [],
    }


# -------------------------------------------------------------------------- brave


def _strip_html(s: str) -> str:
    """Brave wraps query matches in <strong>...</strong>; strip for a clean snippet."""
    if "<" not in s:
        return s
    out: list[str] = []
    depth = 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            if depth > 0:
                depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _brave(query: str, max_results: int, gl: str, hl: str) -> dict[str, Any]:
    """Brave Search API — GET https://api.search.brave.com/res/v1/web/search.

    Real web results from Brave's index via an API (clean provider IP — no datacenter
    CAPTCHA). Free plan: ~1,000 queries/month from the $5/mo credit (credit card
    required to sign up, not charged on the free plan). Paid: $5 / 1,000 requests.
    Key in the X-Subscription-Token header. count ≤ 20.
    """
    params = {
        "q": query,
        "count": min(max_results, 20),
        "country": gl,
        "search_lang": hl,
    }
    headers = {
        "X-Subscription-Token": _key("brave"),
        "Accept-Encoding": "gzip",
    }
    data = http_get_json(
        "https://api.search.brave.com/res/v1/web/search",
        params,
        provider="brave",
        headers=headers,
    )
    web = data.get("web") or {}
    items = web.get("results") or []
    results = [
        {
            "rank": i + 1,
            "title": (it.get("title") or "")[:200],
            "url": it.get("url") or "",
            "snippet": _strip_html(it.get("description") or "")[:300],
        }
        for i, it in enumerate(items)
    ]
    return {"query": query, "results": results, "count": len(results), "provider": "brave"}


_DISPATCH = {
    "google_cse": _google_cse,
    "serper": _serper,
    "serpapi": _serpapi,
    "valueserp": _valueserp,
    "wigolo": _wigolo,
    "brave": _brave,
}


def fetch(query: str, max_results: int, gl: str, hl: str, provider: str | None = None) -> dict[str, Any]:
    """Fetch SERP results from the configured (or given) provider."""
    p = provider or resolve_provider()
    return _DISPATCH[p](query, max_results, gl, hl)
