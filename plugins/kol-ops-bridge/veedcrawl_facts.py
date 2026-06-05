"""Map Veedcrawl REST responses to CAL ``identity.veedcrawl_*`` index facts."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _parse_count(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 0 else None
    if not isinstance(value, str):
        return None
    s = value.strip().upper().replace(",", "").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s.endswith("K"):
        mult = 1_000
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    try:
        n = float(s) * mult
        return int(round(n)) if n > 0 else None
    except ValueError:
        return None


def _views_from_item(item: Mapping[str, Any]) -> Optional[int]:
    stats = item.get("stats")
    if isinstance(stats, dict):
        for key in ("views", "viewCount", "playCount"):
            n = _parse_count(stats.get(key))
            if n:
                return n
    for key in ("viewCount", "views", "playCount"):
        n = _parse_count(item.get(key))
        if n:
            return n
    return None


def _likes_from_item(item: Mapping[str, Any]) -> Optional[int]:
    stats = item.get("stats")
    if isinstance(stats, dict):
        for key in ("likes", "likeCount"):
            n = _parse_count(stats.get(key))
            if n:
                return n
    for key in ("likeCount", "likes"):
        n = _parse_count(item.get(key))
        if n:
            return n
    return None


def _canonical_reel_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") + "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _author_handle_from_search_item(item: Mapping[str, Any]) -> Optional[str]:
    """Normalize author/creator fields from heterogeneous search result shapes."""
    for key in ("username", "handle"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lstrip("@").lower()

    author = item.get("author")
    if isinstance(author, str) and author.strip():
        return author.strip().lstrip("@").lower()
    if isinstance(author, dict):
        for key in ("username", "handle", "name", "uniqueId"):
            raw = author.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip().lstrip("@").lower()

    creator = item.get("creator")
    if isinstance(creator, str) and creator.strip():
        return creator.strip().lstrip("@").lower()
    if isinstance(creator, dict):
        for key in ("username", "handle", "name", "uniqueId"):
            raw = creator.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip().lstrip("@").lower()

    return None


def build_cache_key(operation: str, request: Mapping[str, Any]) -> str:
    """Stable monthly cache key per operation."""
    if operation == "search_social_videos":
        q = str(request.get("q") or "").strip()
        platform = str(request.get("platform") or "").strip().lower()
        limit = int(request.get("limit") or 6)
        raw = json.dumps({"q": q, "platform": platform, "limit": limit}, sort_keys=True)
        return f"search:{hashlib.sha256(raw.encode()).hexdigest()}"

    if operation in ("get_instagram_profile", "get_tiktok_profile"):
        platform_tag = "ig" if operation == "get_instagram_profile" else "tt"
        handle = str(request.get("username") or "").strip().lstrip("@").lower()
        if not handle and request.get("url"):
            host_pat = (
                r"instagram\.com/([^/?#]+)"
                if platform_tag == "ig"
                else r"tiktok\.com/@?([^/?#]+)"
            )
            m = re.search(host_pat, str(request["url"]), re.I)
            handle = (m.group(1) if m else "").lower()
        limit = min(int(request.get("limit") or 12), 24)
        return f"profile:{platform_tag}:{handle}:limit={limit}"

    if operation == "get_video_metadata":
        url = _canonical_reel_url(str(request.get("url") or ""))
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        return f"metadata:{digest}"

    if operation == "extract_from_video":
        url = _canonical_reel_url(str(request.get("url") or ""))
        prompt = str(request.get("prompt") or "").strip()
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        return f"extract:{url}:{digest}"

    raw = json.dumps(dict(request), sort_keys=True, default=str)
    return f"{operation}:{hashlib.sha256(raw.encode()).hexdigest()}"


def identity_facts_from_response(
    operation: str,
    response: Any,
    *,
    cache_month: str,
    cache_key: str,
    blob_ref: Optional[str] = None,
    handle: Optional[str] = None,
    at_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Build ``identity.veedcrawl_*`` facts from a cached or fresh response."""
    facts: dict[str, Any] = {
        "identity.veedcrawl_cache_month": cache_month,
        "identity.veedcrawl_cache_key": cache_key,
        "identity.veedcrawl_fetched_at": at_iso or _now_iso(),
    }
    if blob_ref:
        facts["identity.veedcrawl_storage_ref"] = blob_ref
        facts["identity.veedcrawl_blob_ref"] = blob_ref

    if operation in ("get_instagram_profile", "get_tiktok_profile") and isinstance(response, dict):
        stats = response.get("stats") if isinstance(response.get("stats"), dict) else {}
        followers = _parse_count(stats.get("followers") or response.get("followers"))
        if followers:
            facts["identity.veedcrawl_profile_followers"] = followers
        if handle:
            facts["identity.veedcrawl_profile_handle"] = handle.lstrip("@").lower()
        videos = response.get("videos")
        if isinstance(videos, list):
            reel_stats: list[dict[str, Any]] = []
            for v in videos[:24]:
                if not isinstance(v, dict):
                    continue
                url = v.get("url") or v.get("link")
                if not isinstance(url, str) or not url.strip():
                    continue
                reel_stats.append({
                    "url": url.strip(),
                    "views": _views_from_item(v),
                    "likes": _likes_from_item(v),
                })
            if reel_stats:
                facts["identity.veedcrawl_recent_reels_stats"] = reel_stats

    elif operation == "get_video_metadata" and isinstance(response, dict):
        views = _views_from_item(response)
        likes = _likes_from_item(response)
        if views is not None:
            facts["identity.veedcrawl_last_reel_views"] = views
        if likes is not None:
            facts["identity.veedcrawl_last_reel_likes"] = likes
        url = response.get("url")
        if isinstance(url, str) and url.strip():
            facts["identity.veedcrawl_last_reel_url"] = url.strip()

    elif operation == "extract_from_video" and isinstance(response, dict):
        api = response.get("api_response")
        if isinstance(api, dict):
            result = api.get("resultJson") or api.get("result_json")
        else:
            result = None
        result = result or response.get("result_json") or response.get("resultJson")
        if result is not None:
            facts["identity.veedcrawl_extract_summary"] = _scalar(result)

    elif operation == "search_social_videos" and isinstance(response, list):
        authors: list[str] = []
        for item in response[:20]:
            if not isinstance(item, dict):
                continue
            handle = _author_handle_from_search_item(item)
            if handle and handle not in authors:
                authors.append(handle)
        if authors:
            facts["identity.veedcrawl_search_authors"] = authors[:20]

    return facts
