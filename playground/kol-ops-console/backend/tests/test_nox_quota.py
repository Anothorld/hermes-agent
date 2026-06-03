"""Tests for Nox quota helpers (Console → nox_kol_tool argv)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.nox_quota import fetch_campaign_nox_stats


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
