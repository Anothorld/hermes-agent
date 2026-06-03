"""Tests for campaign_config validation helpers."""

from __future__ import annotations

import pytest


def _cv(bridge_pkg):
    return bridge_pkg.campaign_validation


def test_normalize_int(bridge_pkg):
    norm = _cv(bridge_pkg).normalize_deliverable_count_per_platform
    assert norm(1) == 1


def test_normalize_uniform_dict(bridge_pkg):
    norm = _cv(bridge_pkg).normalize_deliverable_count_per_platform
    assert norm({"instagram": 1, "tiktok": 1}) == 1


def test_normalize_rejects_mixed_dict(bridge_pkg):
    norm = _cv(bridge_pkg).normalize_deliverable_count_per_platform
    with pytest.raises(ValueError, match="single integer"):
        norm({"instagram": 1, "tiktok": 2})


def test_validate_normalizes_uniform_dict(bridge_pkg):
    cv = _cv(bridge_pkg)
    out = cv.validate_campaign_config(
        {
            "product_display_name": "TV Stand",
            "sku_whitelist": ["TS8319"],
            "color_variant_policy": "any_in_whitelist",
            "compensation": {"default_mode": "gifted"},
            "deliverable_platforms": ["instagram"],
            "deliverable_count_per_platform": {"instagram": 2},
            "brief_template_id": "brief-1",
            "audit_standards_md": "x" * 50,
        },
        campaign_id="POVISON-TS-8319",
        sku_regex=cv.DEFAULT_SKU_REGEX,
    )
    assert out["status"] == "ok"
    assert out["normalized"]["deliverable_count_per_platform"] == 2


def test_validate_nox_config_fields(bridge_pkg):
    cv = _cv(bridge_pkg)
    out = cv.validate_campaign_config(
        {
            "product_display_name": "TV Stand",
            "sku_whitelist": ["TS8319"],
            "color_variant_policy": "any_in_whitelist",
            "compensation": {"default_mode": "gifted"},
            "deliverable_platforms": ["instagram"],
            "deliverable_count_per_platform": 1,
            "brief_template_id": "brief-1",
            "audit_standards_md": "x" * 50,
            "nox_quota_enabled": True,
            "nox_monthly_budget": 1800,
            "nox_cache_timezone": "Asia/Shanghai",
        },
        campaign_id="POVISON-TS-8319",
        sku_regex=cv.DEFAULT_SKU_REGEX,
    )
    assert out["status"] == "ok"
    assert out["normalized"]["nox_monthly_budget"] == 1800
    assert out["normalized"]["nox_cache_timezone"] == "Asia/Shanghai"
