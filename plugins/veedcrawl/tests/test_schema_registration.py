"""Veedcrawl tool schemas must survive Hermes schema_sanitizer."""

from __future__ import annotations

import copy

from plugins.veedcrawl.tools import (
    VEEDCRAWL_INSTAGRAM_PROFILE_SCHEMA,
    VEEDCRAWL_SEARCH_SCHEMA,
    as_function_schema,
)
from tools.schema_sanitizer import sanitize_tool_schemas


def _sanitized_parameters(tool_name: str, parameters: dict) -> dict:
    tool = {
        "type": "function",
        "function": as_function_schema(tool_name, parameters),
    }
    out = sanitize_tool_schemas([tool])[0]["function"]["parameters"]
    return out


def test_search_schema_exposes_required_q_after_sanitize():
    params = _sanitized_parameters(
        "veedcrawl_search_social_videos",
        copy.deepcopy(VEEDCRAWL_SEARCH_SCHEMA),
    )
    assert "q" in params.get("properties", {})
    assert params.get("required") == ["q"]


def test_instagram_profile_schema_exposes_required_username_after_sanitize():
    params = _sanitized_parameters(
        "veedcrawl_instagram_profile",
        copy.deepcopy(VEEDCRAWL_INSTAGRAM_PROFILE_SCHEMA),
    )
    assert "username" in params.get("properties", {})
    assert params.get("required") == ["username"]


def test_bare_parameters_object_would_sanitize_to_empty():
    """Regression: pre-fix schemas were bare parameter objects (no wrapper)."""
    bare = copy.deepcopy(VEEDCRAWL_SEARCH_SCHEMA)
    tool = {"type": "function", "function": {**bare, "name": "veedcrawl_search_social_videos"}}
    params = sanitize_tool_schemas([tool])[0]["function"]["parameters"]
    assert params.get("properties") == {}
    assert "required" not in params or not params.get("required")
