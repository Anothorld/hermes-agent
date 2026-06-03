"""Stable cache keys and URL/handle normalization."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

_TRACKING_PARAMS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "si"}
)


def normalize_url(url: str) -> str:
    """Lowercase host, strip tracking query params."""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=False)
    clean_qs = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    query = "&".join(
        f"{k}={clean_qs[k][0]}" for k in sorted(clean_qs) if clean_qs[k]
    )
    netloc = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme or "https", netloc, path, "", query, ""))


def normalize_handle(handle: str) -> str:
    h = handle.strip().lstrip("@").lower()
    return h


def alias_key(platform: str, handle_or_url: str) -> str:
    """Key for ``alias`` table."""
    p = platform.strip().lower()
    if handle_or_url.startswith("http://") or handle_or_url.startswith("https://"):
        return f"{p}|url|{normalize_url(handle_or_url)}"
    return f"{p}|handle|{normalize_handle(handle_or_url)}"


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key_diligence(nox_creator_id: str, dimensions: list[str], lang: str) -> str:
    dims = ",".join(sorted(d.strip() for d in dimensions if d.strip()))
    return f"diligence_pack|{nox_creator_id}|{dims}|{lang}"


def cache_key_contacts(nox_creator_id: str, lang: str) -> str:
    return f"contacts|{nox_creator_id}|{lang}"


def cache_key_search(platform: str, body: Mapping[str, Any], page_num: int) -> str:
    h = hashlib.sha256(stable_json(dict(body)).encode()).hexdigest()[:32]
    return f"creator_search|{platform.lower()}|{h}|p{page_num}"


def cache_key_monitor(video_url: str, project_id: Optional[str] = None) -> str:
    base = hashlib.sha256(normalize_url(video_url).encode()).hexdigest()[:32]
    if project_id:
        return f"monitor_setup|{base}|{project_id}"
    return f"monitor_setup|{base}"


_HANDLE_FROM_URL = re.compile(r"youtube\.com/(?:@|channel/|c/)([^/?]+)", re.I)
