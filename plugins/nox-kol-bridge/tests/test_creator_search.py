"""Tests for creator-search body forwarding."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal import commands  # noqa: E402


def test_creator_search_passes_body(nox_home):
    cfg = {
        "campaign_id": "camp_test",
        "nox_quota_enabled": True,
        "nox_supplement_enabled": True,
        "nox_cache_enabled": True,
        "nox_supplement_max_calls": 10,
    }
    with patch("internal.cli_runner.run_creator_search") as mock_search:
        mock_search.return_value = {
            "success": True,
            "data": {"items": [], "page_num": 1},
        }
        out = commands.cmd_creator_search(
            env="LIVE",
            gate="supplement_search",
            monthly_budget=100,
            tz_name="UTC",
            lang="en",
            platform="youtube",
            body={"keywords": ["cycling"], "page_size": 5},
            page_num=1,
            campaign_config=cfg,
        )
    assert out["api_calls"] == 1
    mock_search.assert_called_once()
    _platform, body = mock_search.call_args[0]
    assert _platform == "youtube"
    assert body["keywords"] == ["cycling"]
    assert body["page_num"] == 1


def test_creator_search_rejects_too_many_platforms(nox_home):
    cfg = {
        "campaign_id": "camp_test",
        "nox_quota_enabled": True,
        "nox_supplement_enabled": True,
    }
    with pytest.raises(ValueError, match="at most 2"):
        commands.cmd_creator_search(
            env="TEST",
            gate="supplement_search",
            monthly_budget=100,
            tz_name="UTC",
            lang="en",
            platform="youtube",
            body={"platforms": ["youtube", "tiktok", "instagram"]},
            page_num=1,
            campaign_config=cfg,
        )
