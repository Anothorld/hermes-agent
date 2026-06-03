"""Tests for Nox campaign_config extraction (flat bridge row vs nested brief)."""

from __future__ import annotations

from app.nox_gate import _nox_quota_is_enabled, extract_campaign_config


def test_extract_flat_bridge_campaign_row() -> None:
    row = {
        "campaign_id": "POVISON-TS-8319",
        "env": "LIVE",
        "nox_quota_enabled": True,
        "deliverable_platforms": ["instagram"],
    }
    cfg = extract_campaign_config(row)
    assert cfg["nox_quota_enabled"] is True
    assert cfg["campaign_id"] == "POVISON-TS-8319"


def test_extract_nested_campaign_config() -> None:
    payload = {
        "campaign_config": {
            "nox_quota_enabled": True,
            "nox_monthly_budget": 500,
        },
    }
    cfg = extract_campaign_config(payload)
    assert cfg["nox_quota_enabled"] is True
    assert cfg["nox_monthly_budget"] == 500


def test_nox_quota_is_enabled_coerces_string() -> None:
    assert _nox_quota_is_enabled({"nox_quota_enabled": "true"})
    assert not _nox_quota_is_enabled({})
    assert not _nox_quota_is_enabled({"nox_quota_enabled": False})
