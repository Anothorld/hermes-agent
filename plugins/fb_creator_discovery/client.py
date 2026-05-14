"""High-level facade over the Meta Creator Discovery Graph API.

The class composes an :class:`FBGraphHTTPClient` (DIP) and exposes one method
per use case. Each method is responsible only for translating Python kwargs
into Graph API query parameters — error handling and HTTP plumbing live in
the transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from ._internal.credentials import (
    EnvFileCredentialProvider,
    FBCredentialProvider,
)
from ._internal.http import FBGraphHTTPClient
from ._internal.params import (
    build_insights_field,
    build_metric_filter,
    coerce_limit,
    coerce_offset,
    csv,
    merge_fields,
    validate_time_range,
)


CREATORS_PATH = "/creator_marketplace/creators"
CONTENT_PATH = "/creator_marketplace/content"

# Default lightweight fields returned by `search_creators` so the agent
# does not have to scroll through huge insights payloads when it only
# needs to triage candidates.
DEFAULT_CREATOR_CARD_FIELDS = (
    "creator_id",
    "creator_display_name",
    "creator_alias",
    "creator_bio",
    "creator_profile_url",
    "creator_follower_count",
    "creator_categories",
    "creator_country",
    "creator_onboarding_status",
)

DEFAULT_CONTENT_CARD_FIELDS = (
    "content_id",
    "creator_id",
    "content_type",
    "content_url",
    "caption",
    "published_time",
)


@dataclass(frozen=True)
class MetricFilter:
    """Reusable container for ``{min,max,time_range,breakdown}`` filters."""

    min_value: Optional[float] = None
    max_value: Optional[float] = None
    time_range: Optional[str] = None
    breakdown: Optional[str] = None

    def to_param(self) -> Optional[str]:
        return build_metric_filter(
            min_value=self.min_value,
            max_value=self.max_value,
            time_range=self.time_range,
            breakdown=self.breakdown,
        )

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> Optional["MetricFilter"]:
        if not raw or not isinstance(raw, Mapping):
            return None
        instance = cls(
            min_value=_safe_number(raw.get("min")),
            max_value=_safe_number(raw.get("max")),
            time_range=raw.get("time_range"),
            breakdown=raw.get("breakdown"),
        )
        if instance.to_param() is None:
            return None
        return instance


class FBCreatorClient:
    """Domain client for Creator Discovery."""

    def __init__(self, http: FBGraphHTTPClient) -> None:
        self._http = http

    def close(self) -> None:
        self._http.close()

    # -- Creators ---------------------------------------------------------

    def search_creators(
        self,
        *,
        query: Optional[str] = None,
        creator_categories: Optional[Sequence[str] | str] = None,
        creator_countries: Optional[Sequence[str] | str] = None,
        interests: Optional[Sequence[str] | str] = None,
        follower_count: Optional[MetricFilter] = None,
        reach: Optional[MetricFilter] = None,
        interaction_rate: Optional[MetricFilter] = None,
        major_audience_age_bucket: Optional[str] = None,
        major_audience_gender: Optional[str] = None,
        fields: Optional[Sequence[str] | str] = None,
        limit: int = 25,
        after: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {
            "query": query,
            "creator_categories": csv(creator_categories),
            "creator_countries": csv(creator_countries),
            "interests": csv(interests),
            "follower_count": follower_count.to_param() if follower_count else None,
            "reach": reach.to_param() if reach else None,
            "interaction_rate": (
                interaction_rate.to_param() if interaction_rate else None
            ),
            "major_audience_age_bucket": major_audience_age_bucket,
            "major_audience_gender": _normalize_gender(major_audience_gender),
            "fields": merge_fields(fields, DEFAULT_CREATOR_CARD_FIELDS),
            "limit": coerce_limit(limit, default=25, maximum=100),
            "after": after,
        }
        return self._http.get(CREATORS_PATH, params=params)

    def get_creator(
        self,
        creator_id: str,
        *,
        fields: Optional[Sequence[str] | str] = None,
        insights_metrics: Optional[Sequence[str] | str] = None,
        insights_period: Optional[str] = None,
        insights_time_range: Optional[str] = None,
        recent_content_limit: Optional[int] = None,
    ) -> Any:
        if not str(creator_id or "").strip():
            raise ValueError("creator_id is required")
        field_groups: list = [fields, DEFAULT_CREATOR_CARD_FIELDS]
        insights_expr = build_insights_field(
            metrics=_as_seq(insights_metrics),
            period=insights_period,
            time_range=insights_time_range,
        )
        if insights_expr:
            field_groups.append(insights_expr)
        if recent_content_limit:
            field_groups.append(
                f"recent_content.limit({coerce_limit(recent_content_limit, default=5, maximum=50)})"
            )
        params: Dict[str, Any] = {
            "creator_id": str(creator_id).strip(),
            "fields": merge_fields(*field_groups),
        }
        return self._http.get(CREATORS_PATH, params=params)

    # -- Content ----------------------------------------------------------

    def search_content(
        self,
        *,
        creator_id: Optional[Sequence[str] | str] = None,
        content_type: Optional[str] = None,
        time_range: Optional[str] = None,
        sort_by: Optional[str] = None,
        reach: Optional[MetricFilter] = None,
        reactions: Optional[MetricFilter] = None,
        comments: Optional[MetricFilter] = None,
        fields: Optional[Sequence[str] | str] = None,
        limit: int = 25,
        after: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {
            "creator_id": csv(creator_id),
            "content_type": _normalize_content_type(content_type),
            "time_range": validate_time_range(time_range),
            "sort_by": _normalize_sort_by(sort_by),
            "reach": reach.to_param() if reach else None,
            "reactions": reactions.to_param() if reactions else None,
            "comments": comments.to_param() if comments else None,
            "fields": merge_fields(fields, DEFAULT_CONTENT_CARD_FIELDS),
            "limit": coerce_limit(limit, default=25, maximum=100),
            "after": after,
        }
        return self._http.get(CONTENT_PATH, params=params)

    def get_content(
        self,
        content_id: str,
        *,
        fields: Optional[Sequence[str] | str] = None,
        insights_metrics: Optional[Sequence[str] | str] = None,
        include_comments: bool = False,
        comments_limit: int = 10,
    ) -> Any:
        if not str(content_id or "").strip():
            raise ValueError("content_id is required")
        field_groups: list = [fields, DEFAULT_CONTENT_CARD_FIELDS]
        insights_expr = build_insights_field(metrics=_as_seq(insights_metrics))
        if insights_expr:
            field_groups.append(insights_expr)
        if include_comments:
            field_groups.append(
                f"comments.limit({coerce_limit(comments_limit, default=10, maximum=100)})"
            )
        params: Dict[str, Any] = {
            "content_id": str(content_id).strip(),
            "fields": merge_fields(*field_groups),
        }
        return self._http.get(CONTENT_PATH, params=params)


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


_ALLOWED_GENDERS = frozenset({"female", "male", "unknown"})
_ALLOWED_CONTENT_TYPES = frozenset({"PHOTOS", "VIDEOS", "REELS", "TEXT", "LINKS"})
_ALLOWED_SORT_BY = frozenset(
    {"reach", "views", "reactions", "comments", "shares", "published_time"}
)


def _normalize_gender(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = str(value).strip().lower()
    return lowered if lowered in _ALLOWED_GENDERS else None


def _normalize_content_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    upper = str(value).strip().upper()
    return upper if upper in _ALLOWED_CONTENT_TYPES else None


def _normalize_sort_by(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = str(value).strip().lower()
    return lowered if lowered in _ALLOWED_SORT_BY else None


def _safe_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_seq(value: Any) -> Optional[Sequence[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return [piece.strip() for piece in value.split(",") if piece.strip()]
    if isinstance(value, (list, tuple)):
        return [str(piece).strip() for piece in value if str(piece).strip()]
    return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_default_client(
    credentials: Optional[FBCredentialProvider] = None,
) -> FBCreatorClient:
    """Construct a client wired with the default env+file credential provider."""
    provider = credentials or EnvFileCredentialProvider()
    http = FBGraphHTTPClient(provider)
    return FBCreatorClient(http)
