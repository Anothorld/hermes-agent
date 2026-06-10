"""Tests for Nox quota helpers (Console → nox_kol_tool argv)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.nox_quota import fetch_campaign_nox_stats, invalidate_nox_stats_cache


@pytest.mark.asyncio
async def test_fetch_campaign_nox_stats_argv_no_env_flag() -> None:
    """``cache-stats`` does not accept ``--env`` (only gated subcommands do)."""
    bridge = AsyncMock()
    bridge.get_campaign.return_value = {
        "campaign_id": "POVISON-TS-8319",
        "nox_cache_timezone": "Asia/Shanghai",
    }
    captured: dict[str, list[str]] = {}

    def _fake_run(argv: list[str], **_kwargs):
        captured["argv"] = argv
        return {
            "cache": {"hits": 0, "misses": 0},
            "usage": {"remaining_estimate": 100},
        }

    with patch("app.nox_quota.run_nox_tool", side_effect=_fake_run):
        stats = await fetch_campaign_nox_stats(
            bridge, "POVISON-TS-8319", env="LIVE",
        )

    assert "--env" not in captured["argv"]
    assert captured["argv"] == [
        "cache-stats",
        "--campaign-id",
        "POVISON-TS-8319",
        "--timezone",
        "Asia/Shanghai",
    ]
    assert stats["quota_exhausted"] is False


@pytest.mark.asyncio
async def test_fetch_campaign_nox_stats_cache_invalidated_on_config_change() -> None:
    """``nox_quota_enabled`` must refresh after ``invalidate_nox_stats_cache``."""
    bridge = AsyncMock()
    bridge.get_campaign.side_effect = [
        {"campaign_id": "C-1", "nox_quota_enabled": False},
        {"campaign_id": "C-1", "nox_quota_enabled": True},
    ]

    def _fake_run(_argv: list[str], **_kwargs):
        return {"usage": {"remaining_estimate": 50}}

    with patch("app.nox_quota.run_nox_tool", side_effect=_fake_run):
        first = await fetch_campaign_nox_stats(bridge, "C-1", env="LIVE")
        assert first["nox_quota_enabled"] is False
        second = await fetch_campaign_nox_stats(bridge, "C-1", env="LIVE")
        assert second["nox_quota_enabled"] is False
        invalidate_nox_stats_cache("C-1", env="LIVE")
        third = await fetch_campaign_nox_stats(bridge, "C-1", env="LIVE")
        assert third["nox_quota_enabled"] is True
