"""Tests for deterministic campaign_config validation."""

from __future__ import annotations


def _cv(bridge_pkg):
    return bridge_pkg.campaign_validation


def _good_candidate(**over):
    base = {
        "product_display_name": "the new media console",
        "sku_whitelist": ["TS8319"],
        "color_variant_policy": "strict_whitelist",
        "compensation": {"default_mode": "paid", "paid_max_amount": 1500},
        "deliverable_platforms": ["instagram", "tiktok"],
        "deliverable_count_per_platform": {"instagram": 1, "tiktok": 1},
        "brief_template_id": "tpl-default",
        "audit_standards_md": "x" * 60,
    }
    base.update(over)
    return base


def test_all_green(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(), campaign_id="TS8319-summer")
    assert out["status"] == "ok"
    assert out["normalized"]["product_display_name"] == "the new media console"


def test_missing_fields(bridge_pkg):
    cand = _good_candidate()
    del cand["sku_whitelist"]
    del cand["audit_standards_md"]
    out = _cv(bridge_pkg).validate_campaign_config(cand, campaign_id="C1")
    assert out["status"] == "incomplete"
    assert "sku_whitelist" in out["missing"]
    assert "audit_standards_md" in out["missing"]


def test_display_name_equal_sku_rejected(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(product_display_name="TS8319"), campaign_id="c")
    assert out["status"] == "invalid"
    assert out["invalid"][0]["field"] == "product_display_name"


def test_display_name_equal_campaign_id_rejected(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(product_display_name="summer-2026"),
        campaign_id="summer-2026")
    assert out["status"] == "invalid"


def test_empty_whitelist_is_missing_not_default(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(sku_whitelist=[]), campaign_id="c")
    assert out["status"] == "incomplete"
    assert "sku_whitelist" in out["missing"]


def test_bad_color_policy_invalid(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(color_variant_policy="any"), campaign_id="c")
    assert out["status"] == "invalid"


def test_short_audit_md_invalid(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(audit_standards_md="too short"), campaign_id="c")
    assert out["status"] == "invalid"


def test_cap_review(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(compensation={"default_mode": "paid",
                                     "paid_max_amount": 2_000_000}),
        campaign_id="c")
    assert out["status"] == "cap_review"
    assert out["amount"] == 2_000_000


def test_cap_review_bypassed_when_confirmed(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(compensation={"default_mode": "paid",
                                     "paid_max_amount": 2_000_000}),
        campaign_id="c", confirmed_high_budget=True)
    assert out["status"] == "ok"


def test_bad_platform_invalid(bridge_pkg):
    out = _cv(bridge_pkg).validate_campaign_config(
        _good_candidate(deliverable_platforms=["instagram", "snapchat"]),
        campaign_id="c")
    assert out["status"] == "invalid"
