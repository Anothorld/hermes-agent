"""Tests for paid_ratio_override from campaign_config."""

from __future__ import annotations


def test_paid_ratio_override_from_campaign_config(bridge_pkg):
    pe = bridge_pkg.pricing_engine
    out = pe.compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1000,
        "barter_attempted": True,
        "kol_insists_paid": True,
        "campaign_config": {
            "paid_ceiling": 1500,
            "paid_ratio_override": 0.58,
        },
    })
    assert out["target_number"] == 580


def test_paid_ratio_override_payload_wins_over_campaign(bridge_pkg):
    pe = bridge_pkg.pricing_engine
    out = pe.compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1000,
        "barter_attempted": True,
        "kol_insists_paid": True,
        "paid_ratio_override": 0.62,
        "campaign_config": {
            "paid_ceiling": 1500,
            "paid_ratio_override": 0.58,
        },
    })
    assert out["target_number"] == 620
