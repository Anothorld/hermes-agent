"""Hard qualification thresholds — single source of truth.

These constants are synced 1:1 with ``instagram-kol-discovery`` SKILL.md
``## Roles And Qualification`` section. When the skill or bridge
thresholds change, the SAME PR must update this file + plugin README
threshold table + skill cross-reference comment.

Priority (hard rule):
    HARD thresholds (this file) > learned criteria > default scoring
"""

from __future__ import annotations

from typing import Final

# --- Followers ---
FOLLOWERS_MIN: Final[int] = 80_000
# 80k-100k borderline: RPA marks gate as pass but flags borderline;
# Agent decides ingest/discard (skill Roles And Qualification)
FOLLOWERS_BORDERLINE_MAX: Final[int] = 100_000

# --- Reels activity ---
REELS_MIN_3MO: Final[int] = 5
REELS_WINDOW_DAYS: Final[int] = 90

# --- Views ---
AVG_VIEWS_MIN: Final[int] = 20_000
# Exclude reels posted within last 72h from avg calculation (skill L140)
VIEWS_EXCLUDE_HOURS: Final[int] = 72

# --- Engagement rate ---
# Reel ER = (likes + comments) / views; must be >= 2%
REEL_ER_MIN: Final[float] = 0.02

# --- Region ---
REGIONS_ALLOWED: Final[frozenset[str]] = frozenset({
    "US", "CA", "United States", "Canada",
})
REGION_UNKNOWN_DISCARD: Final[bool] = True

# --- Furniture self-commerce (furniture-only campaigns) ---
# Heuristic keyword scan of bio / link-in-bio / pinned posts.
# Non-furniture self-commerce (fashion/beauty/food/tech/pet) does NOT trigger.
FURNITURE_SELF_COMMERCE_KEYWORDS: Final[tuple[str, ...]] = (
    "furniture store",
    "shop my furniture",
    "my furniture brand",
    "dtc sofa",
    "own brand furniture",
    "furniture dropshipping",
    "furniture storefront",
    "ltk furniture",
    "shop my",
    "ltk",
)

# Non-furniture self-commerce that does NOT trigger discard
NON_FURNITURE_SELF_COMMERCE_OK: Final[tuple[str, ...]] = (
    "fashion", "beauty", "food", "kitchenware", "decor accessories",
    "tech", "pet",
)

# --- Prior-collab skip list reasons (from bridge list-discovery-skip-handles) ---
SKIP_LIST_REASONS: Final[frozenset[str]] = frozenset({
    "competitor",
    "success",
    "aborted",
    "legacy_collab",
})

# --- Outreach cooldown ---
OUTREACH_COOLDOWN_DAYS: Final[int] = 14

# --- Comment mining (discovery mode) ---
COMMENTER_MIN_FOLLOWERS_HINT: Final[int] = 80_000

# --- Machine-readable discard reason codes ---
DISCARD_REASONS = frozenset({
    "followers_below_min",
    "followers_below_100k",  # legacy alias retained for old diagnostics readers
    "region_unknown",
    "reels_count_below_5",
    "avg_views_below_min",
    "avg_views_below_30k",  # legacy alias
    "reel_er_below_min",
    "reel_er_below_3pct",  # legacy alias
    "already_in_pool",
    "skip_list_active",
    "outreach_cooldown_active",
    "static_only_account",
    "furniture_self_commerce_heuristic",
})

# --- Agent-only judgment items (RPA cannot decide) ---
AGENT_JUDGMENT_REQUIRED = [
    "product_context",
    "match_score",
    "showcase_score",
    "learned_criteria_veto",
]
