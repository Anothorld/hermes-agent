"""Follower count normalization — locale-specific shorthand to absolute int.

Synced with ``instagram-kol-discovery`` SKILL.md L149:
    K/k = 1,000
    M = 1,000,000
    B = 1,000,000,000
    万/w = 10,000
    亿 = 100,000,000

Examples:
    "125K"  → 125000  (passes ≥100k)
    "73.8万" → 738000 (passes ≥100k)
    "4.6万"  → 46000  (fails <100k)
    "1.2M"  → 1200000
    "3.5B"  → 3500000000
"""

from __future__ import annotations

import re

# Match: optional number + optional decimal + optional suffix
# Handles "125K", "125k", "1.2M", "73.8万", "4.6w", "3.5亿", "100000"
_PATTERN = re.compile(
    r"^\s*([\d.,]+)\s*([KkMmBb万亿wW]?)\s*$"
)

_SUFFIX_MULTIPLIERS = {
    "k": 1_000,
    "K": 1_000,
    "m": 1_000_000,
    "M": 1_000_000,
    "b": 1_000_000_000,
    "B": 1_000_000_000,
    "万": 10_000,
    "w": 10_000,
    "W": 10_000,
    "亿": 100_000_000,
}


def normalize_followers(raw: str | int | float | None) -> int:
    """Normalize a follower count string to an absolute integer.

    Args:
        raw: The raw follower text (e.g. "125K", "73.8万") or a number.

    Returns:
        Absolute follower count as int. Returns 0 if parsing fails.

    Examples:
        >>> normalize_followers("125K")
        125000
        >>> normalize_followers("73.8万")
        738000
        >>> normalize_followers("4.6万")
        46000
    """
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)

    text = str(raw).strip().replace(",", "")
    if not text:
        return 0

    # Try direct int parse first
    try:
        return int(text)
    except ValueError:
        pass

    match = _PATTERN.match(text)
    if match is None:
        return 0

    number_str, suffix = match.groups()
    try:
        number = float(number_str)
    except ValueError:
        return 0

    multiplier = _SUFFIX_MULTIPLIERS.get(suffix, 1)
    return int(round(number * multiplier))


def is_borderline(followers: int) -> bool:
    """Check if follower count is in the 100k-110k borderline range.

    Per skill L149-155, Agent must either ingest or discard with explicit
    reason — never "pending verification".
    """
    import sys
    from pathlib import Path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    import qualification_rules as rules
    return rules.FOLLOWERS_MIN <= followers < rules.FOLLOWERS_BORDERLINE_MAX
