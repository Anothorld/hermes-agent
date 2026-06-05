"""Resolve a KOL's public profile URL for operator preview links."""

from __future__ import annotations

import re
from typing import Any, Mapping

_PLATFORM_FACT_KEYS: dict[str, str] = {
    "instagram": "identity.instagram_profile_url",
    "tiktok": "identity.tiktok_profile_url",
    "youtube": "identity.youtube_profile_url",
    "facebook": "identity.facebook_profile_url",
    "twitter": "identity.twitter_profile_url",
    "x": "identity.twitter_profile_url",
    "threads": "identity.threads_profile_url",
}

_PROFILE_FACT_KEYS: tuple[str, ...] = (
    "identity.instagram_profile_url",
    "identity.tiktok_profile_url",
    "identity.youtube_profile_url",
    "identity.facebook_profile_url",
    "identity.twitter_profile_url",
    "identity.threads_profile_url",
    "identity.linktree_url",
    "identity.personal_site_url",
)

_HTTP_URL = re.compile(r"^https?://", re.I)


def _normalize_url(value: Any) -> str | None:
    if isinstance(value, str):
        raw = value
    elif value is not None and not isinstance(value, (dict, list, bool)):
        raw = str(value).strip()
    else:
        return None
    trimmed = raw.strip()
    if not _HTTP_URL.match(trimmed):
        return None
    return trimmed


def guess_profile_url(
    platform: str | None,
    handle: str | None,
) -> str | None:
    """Best-effort URL from platform + handle when CAL has no profile fact."""
    if not handle:
        return None
    h = handle.strip().lstrip("@")
    if not h:
        return None
    p = (platform or "instagram").strip().lower()
    if p == "tiktok":
        return f"https://www.tiktok.com/@{h}"
    if p == "youtube":
        return f"https://www.youtube.com/@{h}"
    if p in {"twitter", "x"}:
        return f"https://x.com/{h}"
    if p == "facebook":
        return f"https://www.facebook.com/{h}"
    if p == "threads":
        return f"https://www.threads.net/@{h}"
    return f"https://www.instagram.com/{h}/"


def resolve_profile_url(
    *,
    platform: str | None = None,
    handle: str | None = None,
    facts: Mapping[str, Any] | None = None,
    fallback_url: str | None = None,
) -> str | None:
    """Prefer CAL profile facts, then bridge fallback, then handle guess."""
    facts_map = facts if isinstance(facts, Mapping) else {}
    plat = (platform or "").strip().lower() or None
    if plat:
        key = _PLATFORM_FACT_KEYS.get(plat)
        if key:
            url = _normalize_url(facts_map.get(key))
            if url:
                return url
    for key in _PROFILE_FACT_KEYS:
        url = _normalize_url(facts_map.get(key))
        if url:
            return url
    fb = _normalize_url(fallback_url)
    if fb:
        return fb
    return guess_profile_url(platform, handle)


PROFILE_URL_FACT_KEYS: list[str] = list(_PROFILE_FACT_KEYS)

# (fact_key, label, short_label) — same order as FE KolSocialQuickLinks.
SOCIAL_LINK_SPECS: tuple[tuple[str, str, str], ...] = (
    ("identity.instagram_profile_url", "Instagram", "IG"),
    ("identity.tiktok_profile_url", "TikTok", "TikTok"),
    ("identity.youtube_profile_url", "YouTube", "YT"),
    ("identity.facebook_profile_url", "Facebook", "FB"),
    ("identity.twitter_profile_url", "X", "X"),
    ("identity.threads_profile_url", "Threads", "Threads"),
    ("identity.linktree_url", "Link-in-bio", "bio"),
    ("identity.personal_site_url", "个人站", "site"),
)

_PLATFORM_SHORT: dict[str, str] = {
    "instagram": "IG",
    "tiktok": "TikTok",
    "youtube": "YT",
    "facebook": "FB",
    "twitter": "X",
    "x": "X",
    "threads": "Threads",
    "blog": "IG",
}


def list_social_links_for_candidate(
    *,
    facts: Mapping[str, Any] | None = None,
    platform: str | None = None,
    handle: str | None = None,
    profile_url: str | None = None,
) -> list[dict[str, str]]:
    """All known profile URLs for shortlist / API (deduped, stable order)."""
    facts_map = facts if isinstance(facts, Mapping) else {}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, label, short_label in SOCIAL_LINK_SPECS:
        url = _normalize_url(facts_map.get(key))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({
            "key": key,
            "label": label,
            "short_label": short_label,
            "url": url,
        })
    if out:
        return out
    fallback = _normalize_url(profile_url) or resolve_profile_url(
        platform=platform,
        handle=handle,
        facts=facts_map,
    )
    if not fallback:
        return []
    plat = (platform or "instagram").strip().lower()
    return [{
        "key": "inferred",
        "label": plat.capitalize() if plat else "Profile",
        "short_label": _PLATFORM_SHORT.get(plat, "主页"),
        "url": fallback,
    }]

# Extra keys batched for shortlist hover cards (followers / Nox / OG cache).
SHORTLIST_PREVIEW_FACT_KEYS: list[str] = [
    *_PROFILE_FACT_KEYS,
    "identity.followers",
    "identity.nox_followers",
    "identity.nox_creator_name",
    "identity.nox_diligence_verdict",
    "identity.profile_og_image_url",
    "identity.profile_og_title",
    "identity.profile_og_description",
    "identity.profile_og_fetched_at",
    "identity.profile_og_source_url",
]
