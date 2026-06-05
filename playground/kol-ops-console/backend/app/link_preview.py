"""Fetch Open Graph metadata for social profile URLs (iframe workaround)."""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger("kol_ops_console.link_preview")

_CACHE_TTL_SEC = 3600.0
_CACHE_MAX = 256
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_ALLOWED_HOST_SUFFIXES = (
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "threads.net",
    "threads.com",
)

# Meta serves richer OG tags to the external-hit crawler.
_CRAWLER_UA = (
    "facebookexternalhit/1.1 "
    "(+http://www.facebook.com/externalhit_uatext.php)"
)

_OG_META_RE = re.compile(
    r'<meta\s+[^>]*?(?:property|name)\s*=\s*["\'](og:[^"\']+)["\']'
    r'[^>]*?content\s*=\s*["\']([^"\']*)["\']'
    r'|'
    r'<meta\s+[^>]*?content\s*=\s*["\']([^"\']*)["\']'
    r'[^>]*?(?:property|name)\s*=\s*["\'](og:[^"\']+)["\']',
    re.IGNORECASE,
)


def _host_allowed(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    host = host.lower()
    return any(host == suf or host.endswith(f".{suf}") for suf in _ALLOWED_HOST_SUFFIXES)


def _parse_og_tags(html_text: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for m in _OG_META_RE.finditer(html_text):
        if m.group(1) and m.group(2) is not None:
            key, val = m.group(1).lower(), m.group(2)
        elif m.group(3) is not None and m.group(4):
            val, key = m.group(3), m.group(4).lower()
        else:
            continue
        if key not in tags and val.strip():
            tags[key] = html.unescape(val.strip())
    return tags


def _cache_get(url: str) -> dict[str, Any] | None:
    entry = _cache.get(url)
    if not entry:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _cache.pop(url, None)
        return None
    return payload


def _cache_set(url: str, payload: dict[str, Any]) -> None:
    if len(_cache) >= _CACHE_MAX:
        oldest = min(_cache.items(), key=lambda x: x[1][0])[0]
        _cache.pop(oldest, None)
    _cache[url] = (time.monotonic() + _CACHE_TTL_SEC, payload)


async def fetch_link_preview(url: str) -> dict[str, Any]:
    """Return OG title/image/description for an allowed profile URL."""
    normalized = url.strip()
    if not normalized or not _host_allowed(normalized):
        return {"ok": False, "reason": "host_not_allowed", "url": normalized}

    cached = _cache_get(normalized)
    if cached is not None:
        return {**cached, "cached": True}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                normalized,
                headers={"User-Agent": _CRAWLER_UA, "Accept": "text/html"},
            )
    except httpx.HTTPError as exc:
        log.warning("link_preview_fetch_failed url=%s err=%s", normalized, exc)
        payload = {
            "ok": False,
            "reason": "fetch_failed",
            "url": normalized,
            "message": str(exc),
        }
        _cache_set(normalized, payload)
        return payload

    if resp.status_code >= 400:
        payload = {
            "ok": False,
            "reason": "http_error",
            "url": normalized,
            "status": resp.status_code,
        }
        _cache_set(normalized, payload)
        return payload

    og = _parse_og_tags(resp.text)
    payload = {
        "ok": bool(og.get("og:image") or og.get("og:title")),
        "url": normalized,
        "title": og.get("og:title"),
        "description": og.get("og:description"),
        "image": og.get("og:image"),
        "site_name": og.get("og:site_name"),
    }
    _cache_set(normalized, payload)
    return payload
