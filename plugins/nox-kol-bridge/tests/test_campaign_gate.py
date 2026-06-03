"""Tests for campaign_config LIVE gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.campaign_gate import (  # noqa: E402
    NoxCampaignGateError,
    assert_live_allowed,
    load_campaign_config_file,
    resolve_monthly_budget,
)
from internal import commands  # noqa: E402


def test_live_requires_campaign_config():
    with pytest.raises(NoxCampaignGateError):
        assert_live_allowed("LIVE", {}, operation="diligence_pack")


def test_supplement_requires_flag(tmp_path):
    cfg = {"nox_quota_enabled": True, "nox_supplement_enabled": False}
    with pytest.raises(NoxCampaignGateError):
        assert_live_allowed("LIVE", cfg, operation="creator_search")


def test_resolve_monthly_budget():
    assert resolve_monthly_budget({"nox_monthly_budget": 500}, 1800) == 500


def test_load_campaign_config_file(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"nox_quota_enabled": True}), encoding="utf-8")
    loaded = load_campaign_config_file(str(p))
    assert loaded["nox_quota_enabled"] is True


def test_live_gate_blocks_diligence(tmp_path):
    with pytest.raises(NoxCampaignGateError):
        commands.cmd_diligence_pack(
            env="LIVE",
            gate="shortlist_confirm",
            monthly_budget=100,
            tz_name="UTC",
            lang="en",
            nox_creator_id="x",
            platform=None,
            url=None,
            channel_id=None,
            dimensions=["profile"],
            include_cooperation=False,
            campaign_config={},
        )
