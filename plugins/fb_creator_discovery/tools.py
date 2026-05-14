"""Hermes tool registrations for the Facebook Creator Discovery plugin.

Each ``_handle_*`` function is a thin parameter-translation layer that calls
:class:`FBCreatorClient` and shapes the result for the agent. All errors are
funnelled through :func:`_fb_tool_error` so the JSON envelope always carries
useful structured fields.
"""

from __future__ import annotations

from typing import Any, Dict

from tools.registry import tool_error, tool_result

from .client import FBCreatorClient, MetricFilter, build_default_client
from ._internal.credentials import EnvFileCredentialProvider
from ._internal.errors import (
    FBCreatorAPIError,
    FBCreatorAuthRequiredError,
    FBCreatorError,
)


COMMON_STRING = {"type": "string"}
COMMON_STRING_ARRAY = {"type": "array", "items": COMMON_STRING}
METRIC_FILTER_SCHEMA = {
    "type": "object",
    "description": (
        "Metric range filter. Subfields: min/max (numeric), time_range "
        "(L1/L7/L14/L28/L90/L180), breakdown (follower/non_follower)."
    ),
    "properties": {
        "min": {"type": "number"},
        "max": {"type": "number"},
        "time_range": COMMON_STRING,
        "breakdown": COMMON_STRING,
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


FB_CREATOR_SEARCH_SCHEMA = {
    "name": "fb_creator_search",
    "description": (
        "Search Facebook creators on the Creator Discovery API. Combine free-text "
        "query with category / country / interest filters and metric filters "
        "(follower_count, reach, interaction_rate). Returns a paginated card list "
        "(use the returned paging.cursors.after with the `after` arg for next page). "
        "For deep insights on a single creator use fb_creator_profile."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free-text search (e.g. 'cookies', 'beauty')."},
            "creator_categories": {
                **COMMON_STRING_ARRAY,
                "description": "List of category strings (case-insensitive).",
            },
            "creator_countries": {
                **COMMON_STRING_ARRAY,
                "description": "ISO country codes, e.g. ['US','CA'].",
            },
            "interests": {
                **COMMON_STRING_ARRAY,
                "description": "Interest tags from the API's available-interests list.",
            },
            "follower_count": METRIC_FILTER_SCHEMA,
            "reach": METRIC_FILTER_SCHEMA,
            "interaction_rate": METRIC_FILTER_SCHEMA,
            "major_audience_age_bucket": {
                **COMMON_STRING,
                "description": "e.g. '18-24', '25-34', '35-44'.",
            },
            "major_audience_gender": {
                **COMMON_STRING,
                "description": "female | male | unknown",
            },
            "fields": {
                **COMMON_STRING_ARRAY,
                "description": "Extra response fields to merge with the default card.",
            },
            "limit": {"type": "integer", "description": "Page size 1-100 (default 25)."},
            "after": {"type": "string", "description": "Pagination cursor from prior call."},
        },
    },
}


FB_CREATOR_PROFILE_SCHEMA = {
    "name": "fb_creator_profile",
    "description": (
        "Fetch a single creator's profile and optional insights metrics by id. "
        "Use after fb_creator_search to deep-dive on a candidate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "creator_id": {"type": "string"},
            "fields": COMMON_STRING_ARRAY,
            "insights_metrics": {
                **COMMON_STRING_ARRAY,
                "description": (
                    "e.g. ['creator_reach','creator_interactions',"
                    "'creator_audience_country','creator_audience_gender']"
                ),
            },
            "insights_period": {
                **COMMON_STRING,
                "description": "e.g. 'day' for daily time series; omit for overall.",
            },
            "insights_time_range": {
                **COMMON_STRING,
                "description": "L1/L7/L14/L28/L90/L180.",
            },
            "recent_content_limit": {
                "type": "integer",
                "description": "If set, embed up to N most recent posts (max 50).",
            },
        },
        "required": ["creator_id"],
    },
}


FB_CONTENT_SEARCH_SCHEMA = {
    "name": "fb_content_search",
    "description": (
        "Search Creator content (posts/reels) with filters and sort options, or "
        "fetch one content item by id (set content_id). Use sort_by + time_range "
        "to surface trending posts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content_id": {"type": "string", "description": "If set, fetch this single content."},
            "creator_id": {
                **COMMON_STRING_ARRAY,
                "description": "One or more creator ids to filter by.",
            },
            "content_type": {
                **COMMON_STRING,
                "description": "PHOTOS | VIDEOS | REELS | TEXT | LINKS",
            },
            "time_range": {**COMMON_STRING, "description": "L1/L7/L14/L28/L90/L180"},
            "sort_by": {
                **COMMON_STRING,
                "description": "reach | views | reactions | comments | shares | published_time",
            },
            "reach": METRIC_FILTER_SCHEMA,
            "reactions": METRIC_FILTER_SCHEMA,
            "comments": METRIC_FILTER_SCHEMA,
            "fields": COMMON_STRING_ARRAY,
            "insights_metrics": {
                **COMMON_STRING_ARRAY,
                "description": "Metrics to embed via insights field (single-content mode).",
            },
            "include_comments": {
                "type": "boolean",
                "description": "Single-content mode: include comments field.",
            },
            "comments_limit": {"type": "integer"},
            "limit": {"type": "integer"},
            "after": {"type": "string"},
        },
    },
}


# ---------------------------------------------------------------------------
# Availability gate
# ---------------------------------------------------------------------------


def _check_fb_creator_available() -> bool:
    """Return True iff a Page Access Token is currently configured.

    The check never performs network I/O. Token contents are not exposed.
    """
    try:
        return EnvFileCredentialProvider().is_configured()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


def _fb_tool_error(exc: Exception) -> str:
    if isinstance(exc, FBCreatorAuthRequiredError):
        return tool_error(str(exc), auth_required=True)
    if isinstance(exc, FBCreatorAPIError):
        return tool_error(
            str(exc),
            status_code=exc.status_code,
            fb_error_code=exc.fb_error_code,
            fb_error_subcode=exc.fb_error_subcode,
            fb_trace_id=exc.fb_trace_id,
            retry_after=exc.retry_after,
        )
    if isinstance(exc, FBCreatorError):
        return tool_error(str(exc))
    if isinstance(exc, ValueError):
        return tool_error(str(exc), invalid_input=True)
    return tool_error(f"FB Creator tool failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _client() -> FBCreatorClient:
    return build_default_client()


def _metric_filter(args: Dict[str, Any], key: str) -> MetricFilter | None:
    return MetricFilter.from_mapping(args.get(key))


def _handle_fb_creator_search(args: Dict[str, Any], **_: Any) -> str:
    client = _client()
    try:
        payload = client.search_creators(
            query=args.get("query"),
            creator_categories=args.get("creator_categories"),
            creator_countries=args.get("creator_countries"),
            interests=args.get("interests"),
            follower_count=_metric_filter(args, "follower_count"),
            reach=_metric_filter(args, "reach"),
            interaction_rate=_metric_filter(args, "interaction_rate"),
            major_audience_age_bucket=args.get("major_audience_age_bucket"),
            major_audience_gender=args.get("major_audience_gender"),
            fields=args.get("fields"),
            limit=args.get("limit", 25),
            after=args.get("after"),
        )
        return tool_result(payload)
    except Exception as exc:  # noqa: BLE001 — funnelled through _fb_tool_error
        return _fb_tool_error(exc)
    finally:
        client.close()


def _handle_fb_creator_profile(args: Dict[str, Any], **_: Any) -> str:
    client = _client()
    try:
        payload = client.get_creator(
            creator_id=args.get("creator_id"),
            fields=args.get("fields"),
            insights_metrics=args.get("insights_metrics"),
            insights_period=args.get("insights_period"),
            insights_time_range=args.get("insights_time_range"),
            recent_content_limit=args.get("recent_content_limit"),
        )
        return tool_result(payload)
    except Exception as exc:  # noqa: BLE001
        return _fb_tool_error(exc)
    finally:
        client.close()


def _handle_fb_content_search(args: Dict[str, Any], **_: Any) -> str:
    client = _client()
    try:
        if args.get("content_id"):
            payload = client.get_content(
                content_id=args["content_id"],
                fields=args.get("fields"),
                insights_metrics=args.get("insights_metrics"),
                include_comments=bool(args.get("include_comments", False)),
                comments_limit=int(args.get("comments_limit", 10) or 10),
            )
        else:
            payload = client.search_content(
                creator_id=args.get("creator_id"),
                content_type=args.get("content_type"),
                time_range=args.get("time_range"),
                sort_by=args.get("sort_by"),
                reach=_metric_filter(args, "reach"),
                reactions=_metric_filter(args, "reactions"),
                comments=_metric_filter(args, "comments"),
                fields=args.get("fields"),
                limit=args.get("limit", 25),
                after=args.get("after"),
            )
        return tool_result(payload)
    except Exception as exc:  # noqa: BLE001
        return _fb_tool_error(exc)
    finally:
        client.close()
