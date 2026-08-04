"""Tests for eval_mode — text / cover / video resolution."""

from __future__ import annotations

import os

import eval_mode


def _clear_env() -> None:
    os.environ.pop("KOL_RPA_VISION_EVAL_ENABLED", None)
    os.environ.pop("KOL_RPA_VIDEO_EVAL_ENABLED", None)


def test_default_is_text():
    _clear_env()
    assert eval_mode.is_vision_eval_enabled(None) is False
    assert eval_mode.resolve_eval_mode(None) == "text"
    assert eval_mode.is_text_mode(None) is True
    assert eval_mode.is_video_mode(None) is False


def test_vision_env_on_defaults_to_cover():
    _clear_env()
    os.environ["KOL_RPA_VISION_EVAL_ENABLED"] = "1"
    assert eval_mode.resolve_eval_mode(None) == "cover"
    os.environ.pop("KOL_RPA_VISION_EVAL_ENABLED", None)


def test_vision_and_video_env_on():
    _clear_env()
    os.environ["KOL_RPA_VISION_EVAL_ENABLED"] = "1"
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "1"
    assert eval_mode.resolve_eval_mode(None) == "video"
    _clear_env()


def test_video_env_ignored_when_vision_off():
    _clear_env()
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "1"
    assert eval_mode.resolve_eval_mode(None) == "text"
    _clear_env()


def test_brief_vision_true_overrides_env():
    _clear_env()
    os.environ["KOL_RPA_VISION_EVAL_ENABLED"] = "0"
    assert eval_mode.resolve_eval_mode({"rpa_vision_eval_enabled": True}) == "cover"
    _clear_env()


def test_brief_video_true_with_vision():
    _clear_env()
    assert (
        eval_mode.resolve_eval_mode(
            {"rpa_vision_eval_enabled": True, "rpa_video_eval_enabled": True}
        )
        == "video"
    )


def test_brief_false_overrides_env():
    _clear_env()
    os.environ["KOL_RPA_VISION_EVAL_ENABLED"] = "1"
    os.environ["KOL_RPA_VIDEO_EVAL_ENABLED"] = "1"
    assert (
        eval_mode.resolve_eval_mode({"rpa_video_eval_enabled": False}) == "cover"
    )
    _clear_env()


def test_constants():
    assert eval_mode.EVAL_COVER_COUNT == 10
    assert eval_mode.EVAL_VIDEO_COUNT == 3
