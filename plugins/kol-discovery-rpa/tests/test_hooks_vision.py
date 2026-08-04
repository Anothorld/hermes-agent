"""Hooks block multimodal tools when vision eval is OFF (discovery-scoped)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))
sys.path.insert(0, str(PLUGIN_DIR / "internal"))

import hooks  # noqa: E402


@pytest.fixture(autouse=True)
def _text_mode(monkeypatch):
    monkeypatch.delenv("KOL_RPA_VISION_EVAL_ENABLED", raising=False)
    monkeypatch.delenv("KOL_RPA_VIDEO_EVAL_ENABLED", raising=False)


def test_blocks_vision_analyze_in_campaign_discovery():
    result = hooks.pre_tool_call(
        "vision_analyze",
        {"image_path": "/tmp/x.jpg", "user_prompt": "x"},
        task_id="kol-campaign:LIVE:t",
    )
    assert result is not None
    assert result["action"] == "block"
    assert "VISION_EVAL" in result["message"] or "caption" in result["message"].lower()


def test_allows_vision_analyze_for_email_discover():
    """Email Tier-2 OCR must keep working while discovery vision is off."""
    result = hooks.pre_tool_call(
        "vision_analyze",
        {"image_path": "/tmp/x.jpg", "user_prompt": "x"},
        task_id="kol-email-discover:LIVE:t",
    )
    assert result is None


def test_allows_vision_analyze_for_creator_brief_refresh():
    result = hooks.pre_tool_call(
        "vision_analyze",
        {"image_path": "/tmp/x.jpg", "user_prompt": "x"},
        task_id="kol-creator-brief-refresh:LIVE:t",
    )
    assert result is None


def test_allows_vision_analyze_for_campaign_draft():
    result = hooks.pre_tool_call(
        "vision_analyze",
        {"image_path": "/tmp/x.jpg", "user_prompt": "x"},
        task_id="kol-campaign-draft:LIVE:t:identity1",
    )
    assert result is None


def test_blocks_download_content_in_text_mode():
    result = hooks.pre_tool_call(
        "rpa_download_ig_content",
        {"content_eval": {}},
        task_id="kol-campaign:LIVE:t",
    )
    assert result is not None
    assert result["action"] == "block"


def test_blocks_download_content_even_outside_discovery():
    """Download tools are multimodal-only; block regardless of session."""
    result = hooks.pre_tool_call(
        "rpa_download_ig_content",
        {"content_eval": {}},
        task_id="kol-email-discover:LIVE:t",
    )
    assert result is not None
    assert result["action"] == "block"


def test_allows_comments_in_text_mode():
    result = hooks.pre_tool_call(
        "rpa_fetch_reel_comments",
        {"reel_url": "https://www.instagram.com/reel/abc/", "mode": "evaluation"},
        task_id="kol-campaign:LIVE:t",
        turn_id="turn-1",
    )
    assert result is None


def test_vision_on_allows_download_content(monkeypatch):
    monkeypatch.setenv("KOL_RPA_VISION_EVAL_ENABLED", "1")
    result = hooks.pre_tool_call(
        "rpa_download_ig_content",
        {"content_eval": {}},
        task_id="kol-campaign:LIVE:t",
        turn_id="turn-2",
    )
    assert result is None


def test_is_campaign_discovery_session_helpers():
    assert hooks._is_campaign_discovery_session(task_id="kol-campaign:LIVE:x")
    assert not hooks._is_campaign_discovery_session(
        task_id="kol-campaign-draft:LIVE:x:id"
    )
    assert not hooks._is_campaign_discovery_session(
        task_id="kol-email-discover:LIVE:x"
    )
