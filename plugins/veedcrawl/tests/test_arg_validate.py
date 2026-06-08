"""Tests for Veedcrawl pre-dispatch argument validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _arg_validate():
    path = PLUGIN_ROOT / "_internal" / "arg_validate.py"
    spec = importlib.util.spec_from_file_location("veedcrawl_arg_validate_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _hooks():
    path = PLUGIN_ROOT / "hooks.py"
    spec = importlib.util.spec_from_file_location("veedcrawl_hooks_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("veedcrawl_instagram_profile", {}),
        ("veedcrawl_instagram_profile", {"username": ""}),
        ("veedcrawl_profile", {}),
        ("veedcrawl_profile", {"platform": "instagram"}),
        ("veedcrawl_search_social_videos", {}),
        ("veedcrawl_search_social_videos", {"q": "  "}),
        ("veedcrawl_extract", {}),
        ("veedcrawl_extract", {"url": "https://example.com/reel/1"}),
        ("veedcrawl_metadata", {}),
    ],
)
def test_validate_rejects_incomplete(tool: str, args: dict) -> None:
    av = _arg_validate()
    message = av.validate_tool_args(tool, args)
    assert message is not None
    assert tool.split("_", 1)[-1] in message or tool in message


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("veedcrawl_instagram_profile", {"username": "creator"}),
        ("veedcrawl_profile", {"platform": "instagram", "username": "creator"}),
        ("veedcrawl_search_social_videos", {"q": "cozy home"}),
        (
            "veedcrawl_extract",
            {
                "url": "https://www.instagram.com/reel/abc/",
                "prompt": "niche",
            },
        ),
        ("veedcrawl_extract", {"job_id": "job-1"}),
        ("veedcrawl_metadata", {"url": "https://www.instagram.com/reel/abc/"}),
    ],
)
def test_validate_accepts_complete(tool: str, args: dict) -> None:
    av = _arg_validate()
    assert av.validate_tool_args(tool, args) is None


def test_hook_blocks_empty_instagram_profile() -> None:
    hooks = _hooks()
    out = hooks.pre_tool_call("veedcrawl_instagram_profile", {})
    assert out is not None
    assert out["action"] == "block"
    assert "username" in out["message"]


def test_hook_allows_complete_search() -> None:
    hooks = _hooks()
    out = hooks.pre_tool_call(
        "veedcrawl_search_social_videos",
        {"q": "living room makeover"},
    )
    assert out is None


def test_hook_ignores_non_veedcrawl_tools() -> None:
    hooks = _hooks()
    assert hooks.pre_tool_call("browser_navigate", {}) is None
