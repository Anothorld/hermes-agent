"""Creator brief readiness assessment (identity-level facts)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Final, Mapping

CREATOR_BRIEF_CORE_KEYS: Final[tuple[str, ...]] = (
    "identity.content_pillars",
    "identity.signature_hooks",
    "identity.voice_descriptors",
    "identity.hero_post_url",
    "identity.hero_post_note",
    "identity.recommendation_reason",
)

CREATOR_BRIEF_STATUS_FACT_KEYS: Final[tuple[str, ...]] = (
    *CREATOR_BRIEF_CORE_KEYS,
    "identity.content_pillars_discovered_at",
)

FRESHNESS_ANCHOR_KEY: Final[str] = "identity.content_pillars_discovered_at"
FRESHNESS_DAYS: Final[int] = 90


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple)):
        return len(value) > 0
    return True


def _parse_iso8601(raw: Any) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def assess_creator_brief_readiness(
    facts: Mapping[str, Any],
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return readiness for outreach personalization (6 keys + 90-day anchor)."""
    missing = [k for k in CREATOR_BRIEF_CORE_KEYS if not _nonempty(facts.get(k))]
    anchor_raw = facts.get(FRESHNESS_ANCHOR_KEY)
    discovered_at = _parse_iso8601(anchor_raw)
    stale = False
    age_days: int | None = None

    if not missing:
        if discovered_at is None:
            stale = True
        else:
            ref = now or dt.datetime.now(dt.timezone.utc)
            if discovered_at.tzinfo is None:
                discovered_at = discovered_at.replace(tzinfo=dt.timezone.utc)
            age = ref - discovered_at
            age_days = max(0, age.days)
            stale = age_days > FRESHNESS_DAYS

    if missing:
        status = "missing"
        ready = False
    elif stale:
        status = "stale"
        ready = False
    else:
        status = "ready"
        ready = True

    return {
        "ready": ready,
        "status": status,
        "missing_keys": missing,
        "stale": stale,
        "discovered_at": anchor_raw if isinstance(anchor_raw, str) else None,
        "age_days": age_days,
    }


def validate_creator_brief_bundle(facts: Mapping[str, Any]) -> list[str]:
    """If any brief key is present, all six must be non-empty."""
    present = [k for k in CREATOR_BRIEF_CORE_KEYS if k in facts]
    if not present:
        return []
    missing = [k for k in CREATOR_BRIEF_CORE_KEYS if not _nonempty(facts.get(k))]
    return missing
