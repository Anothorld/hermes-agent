"""Pure parameter normalization helpers (no I/O, easily unit-testable)."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

# Lookback windows accepted by the Creator Discovery API (see docs).
ALLOWED_TIME_RANGES = frozenset(
    {"L1", "L7", "L14", "L28", "L90", "L180"}
)
ALLOWED_BREAKDOWNS = frozenset({"follower", "non_follower"})


def csv(value: Any) -> Optional[str]:
    """Render ``value`` as a comma-separated string for Graph API params.

    Accepts ``None`` / ``str`` / iterable of strings. Returns ``None`` if the
    result would be empty so the caller can drop the parameter cleanly.
    """
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ",".join(items) if items else None
    return str(value).strip() or None


def coerce_limit(
    raw: Any, *, default: int = 25, minimum: int = 1, maximum: int = 100
) -> int:
    """Clamp ``raw`` to ``[minimum, maximum]`` with ``default`` on parse failure."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def coerce_offset(raw: Any, *, default: int = 0, minimum: int = 0) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def validate_time_range(value: Optional[str]) -> Optional[str]:
    """Return ``value`` upper-cased if it is in the allow-list, else ``None``.

    Invalid values are silently dropped — callers should not pass through user
    input blindly; the upper layer documents the allow-list to the agent.
    """
    if not value:
        return None
    upper = str(value).strip().upper()
    return upper if upper in ALLOWED_TIME_RANGES else None


def build_metric_filter(
    *,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    time_range: Optional[str] = None,
    breakdown: Optional[str] = None,
) -> Optional[str]:
    """Render a metric search filter string.

    The Creator Discovery API uses a JSON-ish syntax such as
    ``{min:1,max:50,time_range:"L28",breakdown:"follower"}``. Subparameter
    string values must be quoted; numeric values must NOT be quoted.

    Returns ``None`` when no fields are set so the caller can omit the param.
    """
    parts: list[str] = []
    if min_value is not None:
        parts.append(f"min:{_format_number(min_value)}")
    if max_value is not None:
        parts.append(f"max:{_format_number(max_value)}")
    tr = validate_time_range(time_range)
    if tr:
        parts.append(f'time_range:"{tr}"')
    if breakdown:
        normalized = str(breakdown).strip().lower()
        if normalized in ALLOWED_BREAKDOWNS:
            parts.append(f'breakdown:"{normalized}"')
    if not parts:
        return None
    return "{" + ",".join(parts) + "}"


def build_insights_field(
    metrics: Optional[Sequence[str]] = None,
    *,
    period: Optional[str] = None,
    time_range: Optional[str] = None,
) -> Optional[str]:
    """Render the ``insights.metrics(...).period(...).time_range(...)`` field.

    Returns ``None`` if no metrics are provided.
    """
    metric_csv = csv(metrics)
    if not metric_csv:
        return None
    expr = f"insights.metrics({metric_csv})"
    if period:
        expr += f".period({str(period).strip()})"
    tr = validate_time_range(time_range)
    if tr:
        expr += f".time_range({tr})"
    return expr


def merge_fields(*field_groups: Optional[Iterable[str] | str]) -> Optional[str]:
    """Merge multiple field lists into one comma-separated string, dedup ordered."""
    seen: dict[str, None] = {}
    for group in field_groups:
        rendered = csv(group)
        if not rendered:
            continue
        for piece in rendered.split(","):
            key = piece.strip()
            if key and key not in seen:
                seen[key] = None
    return ",".join(seen.keys()) if seen else None


def _format_number(value: float) -> str:
    if isinstance(value, bool):  # bool is a subclass of int — exclude it
        raise TypeError("metric filter values may not be bool")
    if isinstance(value, int):
        return str(value)
    # Drop trailing zeros for floats, fall back to repr for safety
    formatted = ("%g" % float(value))
    return formatted
