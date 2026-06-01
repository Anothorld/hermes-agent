"""Tests for the deterministic compensation pricing engine."""

from __future__ import annotations

import pytest


def _pe(bridge_pkg):
    return bridge_pkg.pricing_engine


def _cfg(**over):
    base = {
        "product_unit_price": 200.0,
        "barter_policy": "barter_first",
        "paid_ceiling": 1500.0,
        "commission_band": {"min_pct": 8.0, "max_pct": 12.0,
                            "cookie_days": 30, "attribution": "last_click"},
        "deliverable_count_per_platform": 1,
        "deliverable_platforms": ["instagram"],
    }
    base.update(over)
    return base


def test_gifted_no_quote(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({"mode": "gifted", "campaign_config": _cfg()})
    assert out["mode_decided"] == "gifted"
    assert out["target_number"] is None
    assert out["requires_human_gate"] is False


def test_paid_over_ceiling_gates(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid", "kol_quoted_amount": 1800, "campaign_config": _cfg(),
    })
    assert out["requires_human_gate"] is True
    assert out["gate_reason"] == "paid_quote_over_ceiling"
    assert out["target_number"] is None


def test_paid_within_ceiling_counters_below_cap(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid", "kol_quoted_amount": 1000, "campaign_config": _cfg(),
    })
    assert out["requires_human_gate"] is False
    # min(1000*0.7, 1500*0.8) = 700
    assert out["target_number"] == 700.0
    # never at or above the ceiling
    assert out["target_number"] < 1500.0


def test_paid_no_ceiling_gates(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid", "kol_quoted_amount": 500,
        "campaign_config": _cfg(paid_ceiling=None),
    })
    assert out["gate_reason"] == "missing_paid_ceiling"


def test_commission_within_band_accepts(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "commission", "kol_quoted_amount": 10,
        "kol_quoted_basis": "percent", "campaign_config": _cfg(),
    })
    assert out["mode_decided"] == "commission"
    assert out["target_number"] == 10.0
    assert out["lower_bound"] == 8.0 and out["upper_bound"] == 12.0


def test_commission_over_max_counters_at_max(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "commission", "kol_quoted_amount": 20,
        "campaign_config": _cfg(),
    })
    assert out["target_number"] == 12.0
    assert out["requires_human_gate"] is False


def test_commission_band_as_fraction_normalised(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "commission", "kol_quoted_amount": 10,
        "campaign_config": _cfg(commission_band={"min": 0.08, "max": 0.12}),
    })
    assert out["lower_bound"] == 8.0 and out["upper_bound"] == 12.0


def test_hybrid_cash_over_tier_gates(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "hybrid", "kol_quoted_amount": 1000, "campaign_config": _cfg(),
    })
    assert out["requires_human_gate"] is True
    assert out["gate_reason"] == "hybrid_cash_over_tier"


def test_hybrid_small_cash_supplement(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "hybrid", "kol_quoted_amount": 300, "campaign_config": _cfg(),
    })
    # 200 < 300 <= 400 → supplement = 200*0.3 = 60
    assert out["target_number"] == 60.0
    assert out["requires_human_gate"] is False


def test_invalid_mode_raises(bridge_pkg):
    with pytest.raises(bridge_pkg.pricing_engine.PricingInputError):
        _pe(bridge_pkg).compute_offer({"mode": "barter"})
