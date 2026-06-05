"""Profile Open Graph cache in CAL identity facts."""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping
from urllib.parse import urlparse

PROFILE_OG_FACT_KEYS: tuple[str, ...] = (
    "identity.profile_og_image_url",
    "identity.profile_og_title",
    "identity.profile_og_description",
    "identity.profile_og_fetched_at",
    "identity.profile_og_source_url",
)

OG_CACHE_TTL_DAYS = 7
OG_WRITE_SOURCE = "console:profile_og_cache"


def normalize_profile_url(url: str) -> str:
    """Lowercase host + path for stable cache matching."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "/").rstrip("/") or ""
    return f"{parsed.scheme.lower()}://{host}{path}"


def _parse_fetched_at(raw: Any) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def og_cache_is_fresh(
    facts: Mapping[str, Any] | None,
    profile_url: str,
    *,
    ttl_days: int = OG_CACHE_TTL_DAYS,
) -> bool:
    """True when CAL has OG facts for this exact profile URL within TTL."""
    if not facts or not profile_url.strip():
        return False
    cached_url = facts.get("identity.profile_og_source_url")
    if not isinstance(cached_url, str) or not cached_url.strip():
        return False
    if normalize_profile_url(cached_url) != normalize_profile_url(profile_url):
        return False
    fetched = _parse_fetched_at(facts.get("identity.profile_og_fetched_at"))
    if fetched is None:
        return False
    age = dt.datetime.now(dt.timezone.utc) - fetched
    return age <= dt.timedelta(days=ttl_days)


def link_preview_from_facts(
    facts: Mapping[str, Any] | None,
    profile_url: str,
) -> dict[str, Any] | None:
    """Build link-preview API shape from CAL cache."""
    if not og_cache_is_fresh(facts, profile_url):
        return None
    assert facts is not None
    image = facts.get("identity.profile_og_image_url")
    title = facts.get("identity.profile_og_title")
    description = facts.get("identity.profile_og_description")
    if not (
        (isinstance(image, str) and image.strip())
        or (isinstance(title, str) and title.strip())
    ):
        return None
    return {
        "ok": True,
        "url": profile_url.strip(),
        "title": title if isinstance(title, str) else None,
        "description": description if isinstance(description, str) else None,
        "image": image if isinstance(image, str) else None,
        "cached": True,
        "source": "cal_cache",
    }


def facts_from_link_preview(profile_url: str, preview: Mapping[str, Any]) -> dict[str, str]:
    """CAL facts to persist after a successful OG fetch."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out: dict[str, str] = {
        "identity.profile_og_source_url": profile_url.strip(),
        "identity.profile_og_fetched_at": now,
    }
    if isinstance(preview.get("image"), str) and preview["image"].strip():
        out["identity.profile_og_image_url"] = preview["image"].strip()
    if isinstance(preview.get("title"), str) and preview["title"].strip():
        out["identity.profile_og_title"] = preview["title"].strip()[:300]
    if isinstance(preview.get("description"), str) and preview["description"].strip():
        out["identity.profile_og_description"] = preview["description"].strip()[:600]
    return out
