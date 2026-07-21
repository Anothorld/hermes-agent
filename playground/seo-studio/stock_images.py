"""Stock image search for SEO Studio body images (Pixabay + Openverse).

Deterministic Bridge/CLI wrapper so the agent does not invent image URLs or
pick loosely related browser results. Keys come from env (never hardcode):

- ``PIXABAY_API_KEY`` — Pixabay API key (https://pixabay.com/api/docs/)
- Openverse needs no key for anonymous search (https://api.openverse.org/);
  optional ``OPENVERSE_ACCESS_TOKEN`` raises rate limits if registered.

``auto`` tries both sources and merges unique candidates. If Pixabay is not
configured, Openverse alone still satisfies ``configured: true``.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

_UA = "seo-studio-stock-images/2.0 (+https://github.com/povison; furniture blog tooling)"
_TIMEOUT = 20
_OPENVERSE_BASE = "https://api.openverse.org/v1/images/"
_PIXABAY_BASE = "https://pixabay.com/api/"


def config_snapshot() -> dict[str, Any]:
    """Non-secret status for health / UI."""
    pixabay = bool(os.environ.get("PIXABAY_API_KEY", "").strip())
    # Openverse anonymous search works without a token; token only raises limits.
    openverse = True
    openverse_authed = bool(os.environ.get("OPENVERSE_ACCESS_TOKEN", "").strip())
    return {
        "pixabay_configured": pixabay,
        "openverse_configured": openverse,
        "openverse_authed": openverse_authed,
        "configured": pixabay or openverse,
        "default_source": "pixabay" if pixabay else "openverse",
        # Backward-compatible aliases (old Unsplash/Pexels health consumers)
        "unsplash_configured": False,
        "pexels_configured": False,
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
    """Search Pixabay and/or Openverse for stock photos.

    Args:
        query: English search phrase (prefer concrete furniture/room terms).
        source: ``auto`` | ``pixabay`` | ``openverse``. ``auto`` queries both
            (when Pixabay is keyed) and merges unique results.
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
    # Accept legacy source names so old agent prompts still work.
    if src in ("unsplash", "pexels"):
        src = "auto"
    snap = config_snapshot()
    if not snap["configured"]:
        return {
            "ok": False,
            "error": "No stock image source available. Set PIXABAY_API_KEY and/or rely on Openverse.",
            "candidates": [],
            "config": snap,
        }

    tried: list[str] = []
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []

    order: list[str]
    if src == "pixabay":
        order = ["pixabay"]
    elif src == "openverse":
        order = ["openverse"]
    else:
        order = []
        if snap["pixabay_configured"]:
            order.append("pixabay")
        order.append("openverse")

    # Fetch a bit more per source so merge has headroom after dedupe.
    fetch_n = n if src != "auto" else max(n, min(n + 2, 10))

    for name in order:
        tried.append(name)
        try:
            if name == "pixabay":
                batch = _search_pixabay(q, fetch_n)
            else:
                batch = _search_openverse(q, fetch_n)
            candidates.extend(batch)
            # Single-source mode: stop once we have enough.
            if src != "auto" and candidates:
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
            "config": snap,
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

    sources_used = sorted({c["source"] for c in unique if c.get("source")})
    return {
        "ok": True,
        "query": q,
        "source": ",".join(sources_used) if sources_used else src,
        "tried": tried,
        "candidates": unique,
        "errors": errors or None,
    }


def _search_pixabay(query: str, per_page: int) -> list[dict[str, Any]]:
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("PIXABAY_API_KEY not set")
    # Pixabay per_page minimum is 3
    n = max(3, min(int(per_page), 200))
    params = {
        "key": key,
        "q": query[:100],
        "image_type": "photo",
        "orientation": "horizontal",
        "safesearch": "true",
        "per_page": n,
    }
    resp = requests.get(
        _PIXABAY_BASE,
        params=params,
        headers={"User-Agent": _UA},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    out: list[dict[str, Any]] = []
    for item in data.get("hits") or []:
        # largeImageURL ~1280px; webformatURL ~640px — prefer large for blog
        direct = item.get("largeImageURL") or item.get("webformatURL") or ""
        if not direct:
            continue
        name = item.get("user") or "Pixabay"
        tags = (item.get("tags") or query or "")[:160]
        out.append(
            {
                "url": direct,
                "thumb": item.get("previewURL") or item.get("webformatURL") or direct,
                "alt": tags,
                "photographer": name,
                "credit": f"Photo: Pixabay / {name}",
                "page_url": item.get("pageURL") or "",
                "source": "pixabay",
            }
        )
    return out


def _search_openverse(query: str, per_page: int) -> list[dict[str, Any]]:
    """Search Openverse (CC-licensed) images; no API key required for anonymous use."""
    n = max(1, min(int(per_page), 20))
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    token = os.environ.get("OPENVERSE_ACCESS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Try strict filters first (commercial + photo + wide), then relax if empty.
    attempts: list[dict[str, Any]] = [
        {
            "q": query,
            "page_size": n,
            "page": 1,
            "license_type": "commercial",
            "category": "photograph",
            "aspect_ratio": "wide",
            "mature": "false",
        },
        {
            "q": query,
            "page_size": n,
            "page": 1,
            "license_type": "commercial",
            "mature": "false",
        },
        {
            "q": query,
            "page_size": n,
            "page": 1,
            "mature": "false",
        },
    ]

    data: dict[str, Any] = {}
    last_err: Exception | None = None
    for params in attempts:
        try:
            resp = requests.get(
                _OPENVERSE_BASE,
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 400:
                continue
            resp.raise_for_status()
            data = resp.json() or {}
            if data.get("results"):
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    else:
        if last_err:
            raise last_err
        data = data or {}

    out: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        direct = (item.get("url") or "").strip()
        if not direct or not direct.startswith("http"):
            continue
        creator = (item.get("creator") or item.get("provider") or "Openverse").strip()
        title = (item.get("title") or query or "")[:160]
        license_code = (item.get("license") or "").strip()
        credit_bits = [f"Photo: Openverse / {creator}"]
        if license_code:
            credit_bits.append(f"({license_code})")
        out.append(
            {
                "url": direct,
                "thumb": item.get("thumbnail") or direct,
                "alt": title,
                "photographer": creator,
                "credit": " ".join(credit_bits),
                "page_url": item.get("foreign_landing_url") or item.get("detail_url") or "",
                "source": "openverse",
                "license": license_code,
            }
        )
    return out
