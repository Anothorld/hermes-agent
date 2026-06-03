"""Pydantic-style literals and config for nox-kol-bridge."""

from __future__ import annotations

from typing import Final, Literal

Gate = Literal[
    "shortlist_confirm",
    "pre_outreach_confirm",
    "post_publish_confirm",
    "supplement_search",
]

GATES_REQUIRING_AUDIT: Final[frozenset[str]] = frozenset(
    {
        "shortlist_confirm",
        "pre_outreach_confirm",
        "post_publish_confirm",
        "supplement_search",
    }
)

OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "diligence_pack",
        "contacts",
        "creator_search",
        "monitor_setup",
    }
)

DEFAULT_MONTHLY_BUDGET: Final[int] = 1800
DEFAULT_RETAIN_MONTHS: Final[int] = 3
DEFAULT_TIMEZONE: Final[str] = "Asia/Shanghai"
MAX_SUPPLEMENT_PLATFORMS_PER_RUN: Final[int] = 2
RESPONSE_BLOB_THRESHOLD_BYTES: Final[int] = 100_000
