"""Stock image search for SEO Studio body images (Unsplash + Pexels).

Deterministic Bridge/CLI wrapper so the agent does not invent image URLs or
pick loosely related browser results. Keys come from env (never hardcode):

- ``UNSPLASH_ACCESS_KEY`` — Unsplash Access Key (required for Unsplash)
- ``PEXELS_API_KEY`` — Pexels API key (required for Pexels)

Either source may be missing; search uses whichever is configured. If neither
is set, returns a clear ``configured: false`` error for the operator.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote_plus

import requests

_UA = "seo-studio-stock-images/1.0"
_TIMEOUT = 20


def config_snapshot() -> dict[str, Any]:
    """Non-secret status for health / UI."""
    unsplash = bool(os.environ.get("UNSPLASH_ACCESS_KEY", "").strip())
    pexels = bool(os.environ.get("PEXELS_API_KEY", "").strip())
    return {
        "unsplash_configured": unsplash,
        "pexels_configured": pexels,
        "configured": unsplash or pexels,
        "default_source": "unsplash" if unsplash else ("pexels" if pexels else None),
    }


def _normalize_query(query: str) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip())
    return q[:120]


def search_stock_images(
    query: str,
    *,
    source: str = "auto",
    per_page: int = 5,
) -> dict[str, Any]:
    """Search Unsplash and/or Pexels for stock photos.

    Args:
        query: English search phrase (prefer concrete furniture/room terms).
        source: ``auto`` | ``unsplash`` | ``pexels``. ``auto`` tries Unsplash
            first, then Pexels if Unsplash is empty/unavailable.
        per_page: Candidates to return (1–10).

    Returns:
        Dict with ``ok``, ``query``, ``source``, ``candidates`` (list of
        ``{url, thumb, alt, photographer, credit, page_url, source}``), and
        optional ``error``.
    """
    q = _normalize_query(query)
    if not q:
        return {"ok": False, "error": "query is required", "candidates": []}
    n = max(1, min(int(per_page or 5), 10))
    src = (source or "auto").strip().lower()
    snap = config_snapshot()
    if not snap["configured"]:
        return {
            "ok": False,
            "error": "No stock API keys. Set UNSPLASH_ACCESS_KEY and/or PEXELS_API_KEY.",
            "candidates": [],
            "config": snap,
        }

    tried: list[str] = []
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []

    order: list[str]
    if src == "unsplash":
        order = ["unsplash"]
    elif src == "pexels":
        order = ["pexels"]
    else:
        order = []
        if snap["unsplash_configured"]:
            order.append("unsplash")
        if snap["pexels_configured"]:
            order.append("pexels")

    for name in order:
        tried.append(name)
        try:
            if name == "unsplash":
                batch = _search_unsplash(q, n)
            else:
                batch = _search_pexels(q, n)
            candidates.extend(batch)
            if candidates:
                break
        except Exception as e:  # noqa: BLE001 — surface to operator, keep trying
            errors.append(f"{name}: {e}")

    if not candidates:
        return {
            "ok": False,
            "query": q,
            "source": src,
            "tried": tried,
            "candidates": [],
            "error": "; ".join(errors) or "no results",
        }

    # Dedupe by URL
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in candidates:
        u = c.get("url") or ""
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(c)
        if len(unique) >= n:
            break

    return {
        "ok": True,
        "query": q,
        "source": unique[0]["source"] if unique else src,
        "tried": tried,
        "candidates": unique,
        "errors": errors or None,
    }


def _search_unsplash(query: str, per_page: int) -> list[dict[str, Any]]:
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY not set")
    url = (
        "https://api.unsplash.com/search/photos"
        f"?query={quote_plus(query)}&per_page={per_page}&orientation=landscape"
    )
    resp = requests.get(
        url,
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1", "User-Agent": _UA},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    out: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        urls = item.get("urls") or {}
        user = item.get("user") or {}
        # Prefer regular (suitable for blog); fall back to full/small
        direct = urls.get("regular") or urls.get("full") or urls.get("small") or ""
        if not direct:
            continue
        name = user.get("name") or user.get("username") or "Unsplash"
        out.append(
            {
                "url": direct,
                "thumb": urls.get("thumb") or urls.get("small") or direct,
                "alt": (item.get("alt_description") or item.get("description") or query or "")[:160],
                "photographer": name,
                "credit": f"Photo: Unsplash / {name}",
                "page_url": item.get("links", {}).get("html") or "",
                "source": "unsplash",
            }
        )
    return out


def _search_pexels(query: str, per_page: int) -> list[dict[str, Any]]:
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("PEXELS_API_KEY not set")
    url = (
        "https://api.pexels.com/v1/search"
        f"?query={quote_plus(query)}&per_page={per_page}&orientation=landscape"
    )
    resp = requests.get(
        url,
        headers={"Authorization": key, "User-Agent": _UA},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    out: list[dict[str, Any]] = []
    for item in data.get("photos") or []:
        src = item.get("src") or {}
        # large is a good blog size; original can be huge
        direct = src.get("large") or src.get("large2x") or src.get("original") or ""
        if not direct:
            continue
        name = item.get("photographer") or "Pexels"
        alt = item.get("alt") or query or ""
        out.append(
            {
                "url": direct,
                "thumb": src.get("tiny") or src.get("small") or direct,
                "alt": alt[:160],
                "photographer": name,
                "credit": f"Photo: Pexels / {name}",
                "page_url": item.get("url") or "",
                "source": "pexels",
            }
        )
    return out
