"""Tests for eval_mode — video/cover switch resolution."""

from __future__ import annotations

import os

import eval_mode


def test_default_is_cover():
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)
    assert eval_mode.resolve_eval_mode(None) == "cover"
    assert eval_mode.is_video_mode(None) == False


def test_brief_true_overrides_env():
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "0"
    assert eval_mode.resolve_eval_mode({"rpa_video_eval_enabled": True}) == "video"
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)


def test_brief_false_overrides_env():
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "1"
    assert eval_mode.resolve_eval_mode({"rpa_video_eval_enabled": False}) == "cover"
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)


def test_env_on():
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "1"
    assert eval_mode.resolve_eval_mode(None) == "video"
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)


def test_env_off():
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "0"
    assert eval_mode.resolve_eval_mode(None) == "cover"
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)


def test_env_true_string():
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "true"
    assert eval_mode.resolve_eval_mode(None) == "video"
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)


def test_constants():
    assert eval_mode.EVAL_COVER_COUNT == 10
    assert eval_mode.EVAL_VIDEO_COUNT == 3
