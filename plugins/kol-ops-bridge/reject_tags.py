"""Controlled vocabulary for operator draft-rejection learning tags."""

from __future__ import annotations

from typing import Final

REJECT_TAGS: Final[frozenset[str]] = frozenset({
    "tone_too_salesy",
    "premature_pricing",
    "wrong_sku",
    "over_promised",
    "ignored_question",
    "wrong_language",
    "too_long",
    "factual_error",
    "other",
})

DEFAULT_REJECT_TAG: Final[str] = "other"


def normalize_reject_tags(raw: list[str] | None) -> list[str]:
    """Return deduplicated valid tags; unknown values map to ``other``."""
    if not raw:
        return [DEFAULT_REJECT_TAG]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item or "").strip().lower()
        if not tag:
            continue
        if tag not in REJECT_TAGS:
            tag = DEFAULT_REJECT_TAG
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out or [DEFAULT_REJECT_TAG]
