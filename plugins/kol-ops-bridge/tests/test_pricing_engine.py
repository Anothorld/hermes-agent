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


def _direct(**over):
    base = {"contact_type": "direct", "identity_integrity": "matched"}
    base.update(over)
    return base


def _agency(**over):
    base = {"contact_type": "agency", "identity_integrity": "delegated"}
    base.update(over)
    return base


def test_gifted_no_quote(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({"mode": "gifted", "campaign_config": _cfg()})
    assert out["mode_decided"] == "gifted"
    assert out["target_number"] is None
    assert out["requires_human_gate"] is False
    assert out["negotiation_phase"] == "barter_first"


def test_direct_kol_paid_first_try_barter(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1500,
        "campaign_config": _cfg(),
        **_direct(barter_attempted=False),
    })
    assert out["mode_decided"] == "gifted"
    assert out["target_number"] is None
    assert out["negotiation_phase"] == "barter_first"
    assert out["requires_human_gate"] is False


def test_direct_kol_insists_paid_after_barter_requests_rate(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "campaign_config": _cfg(),
        **_direct(barter_attempted=True, paid_hold_sent=False, kol_insists_paid=True),
    })
    assert out["negotiation_phase"] == "rate_request"
    assert out["target_number"] is None
    assert out["requires_human_gate"] is False
    assert "leanest" in out["suggested_wording"]
    assert "feel workable" in out["suggested_wording"]


def test_direct_kol_paid_counter_after_rate_request(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1000,
        "campaign_config": _cfg(),
        **_direct(barter_attempted=True, rate_requested=True, kol_insists_paid=True),
    })
    assert out["negotiation_phase"] == "paid_counter"
    assert out["requires_human_gate"] is False
    # First cash counter starts from the lowest anchor, not quote ratio.
    assert out["target_number"] == 500
    assert out["target_number"] < 1500.0


def test_direct_kol_high_quote_after_rate_request_uses_campaign_economics_wording(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "campaign_config": _cfg(paid_ceiling=2000),
        **_direct(barter_attempted=True, rate_requested=True, kol_insists_paid=True),
    })
    assert out["negotiation_phase"] == "paid_counter"
    assert out["requires_human_gate"] is False
    assert out["target_number"] == 800
    assert out["mode_decided"] == "hybrid"
    wording = out["suggested_wording"].lower()
    assert "first single-campaign test together" in wording
    assert "future multi-campaign collaborations with us" in wording
    assert "quoted rate" not in wording


def test_pure_cash_quote_below_product_value_still_counters(bridge_pkg):
    """KOL cash ask <= product value must not auto-accept; still negotiate cash."""
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 2000,
        "campaign_config": _cfg(
            product_unit_price=2500.0,
            paid_target_budget=500.0,
            paid_ceiling=1500.0,
        ),
        **_direct(barter_attempted=True, kol_insists_paid=True),
    })
    assert out["target_number"] == 500
    assert out["mode_decided"] == "hybrid"
    wording = out["suggested_wording"].lower()
    assert "first single-campaign test together" in wording
    assert "moving forward at usd 500" in wording


def test_product_value_cash_supplement_professional_counter(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "campaign_config": _cfg(
            product_unit_price=2500.0,
            paid_target_budget=500.0,
            paid_ceiling=1500.0,
        ),
        **_direct(barter_attempted=True, kol_insists_paid=True),
    })
    wording = out["suggested_wording"].lower()
    assert out["target_number"] == 500
    assert out["lower_bound"] == 500
    assert out["upper_bound"] == 1500
    assert "retail value around usd 2500" in wording
    assert "first single-campaign test together" in wording
    assert "moving forward at usd 500" in wording
    assert ".0" not in out["suggested_wording"]


def test_agency_skips_barter_counters_paid(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 2000,
        "campaign_config": _cfg(paid_ceiling=2000),
        **_agency(barter_attempted=False),
    })
    assert out["negotiation_phase"] == "paid_counter"
    assert out["requires_human_gate"] is False
    assert out["target_number"] == 800


def test_paid_over_ceiling_auto_counters_agency(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1800,
        "campaign_config": _cfg(paid_target_budget=500.0),
        **_agency(barter_attempted=True, rate_requested=True),
    })
    assert out["requires_human_gate"] is False
    assert out["negotiation_phase"] == "paid_counter"
    assert out["target_number"] == 500
    wording = out["suggested_wording"].lower()
    assert "first single-campaign category test" in wording
    assert "future multi-campaign collaborations on our slate" in wording


def test_direct_kol_quoted_round1_insists_round2_counters(bridge_pkg):
    """Round-1 quote + round-2 paid insistence skips rate_request (expected)."""
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1500,
        "campaign_config": _cfg(paid_target_budget=500.0),
        **_direct(barter_attempted=True, rate_requested=False, kol_insists_paid=True),
    })
    assert out["negotiation_phase"] == "paid_counter"
    assert out["target_number"] == 500
    assert out["requires_human_gate"] is False


def test_direct_kol_over_ceiling_still_barters_first(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "campaign_config": _cfg(paid_ceiling=2000),
        **_direct(barter_attempted=False),
    })
    assert out["mode_decided"] == "gifted"
    assert out["negotiation_phase"] == "barter_first"
    assert out["requires_human_gate"] is False


def test_paid_within_ceiling_counters_below_cap(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1000,
        "campaign_config": _cfg(),
        **_agency(barter_attempted=True, paid_hold_sent=True),
    })
    assert out["requires_human_gate"] is False
    assert out["target_number"] == 500
    assert out["target_number"] < 1500.0


def test_paid_no_ceiling_gates(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 500,
        "campaign_config": _cfg(paid_ceiling=None),
        **_agency(),
    })
    assert out["gate_reason"] == "missing_paid_ceiling"


def test_commission_within_band_accepts(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "commission",
        "kol_quoted_amount": 10,
        "kol_quoted_basis": "percent",
        "campaign_config": _cfg(),
        **_agency(barter_attempted=True),
    })
    assert out["mode_decided"] == "commission"
    assert out["target_number"] == 9.0
    assert out["lower_bound"] == 8.0 and out["upper_bound"] == 12.0


def test_commission_over_max_counters_at_max(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "commission",
        "kol_quoted_amount": 20,
        "campaign_config": _cfg(),
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 12.0
    assert out["requires_human_gate"] is False


def test_commission_band_as_fraction_normalised(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "commission",
        "kol_quoted_amount": 10,
        "campaign_config": _cfg(commission_band={"min": 0.08, "max": 0.12}),
        **_agency(barter_attempted=True),
    })
    assert out["lower_bound"] == 8.0 and out["upper_bound"] == 12.0


def test_hybrid_post_barter_counters_like_paid(bridge_pkg):
    """Hybrid after barter uses the same cash-supplement counter as paid mode."""
    out = _pe(bridge_pkg).compute_offer({
        "mode": "hybrid",
        "kol_quoted_amount": 1000,
        "campaign_config": _cfg(),
        **_agency(barter_attempted=True),
    })
    assert out["requires_human_gate"] is False
    assert out["negotiation_phase"] == "paid_counter"
    assert out["target_number"] == 500
    assert out["mode_decided"] == "hybrid"


def test_hybrid_small_cash_supplement(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "hybrid",
        "kol_quoted_amount": 300,
        "campaign_config": _cfg(),
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 100
    assert out["requires_human_gate"] is False


def test_rate_requested_without_quote_does_not_counter(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "campaign_config": _cfg(paid_target_budget=500),
        **_direct(barter_attempted=True, rate_requested=True, kol_insists_paid=False),
    })
    assert out["negotiation_phase"] == "rate_request"
    assert out["target_number"] is None
    assert "instead of guessing" in out["suggested_wording"]


def test_direct_kol_barter_attempted_without_paid_insist_does_not_counter(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "campaign_config": _cfg(paid_target_budget=500),
        **_direct(barter_attempted=True, rate_requested=False, kol_insists_paid=False),
    })
    assert out["negotiation_phase"] == "rate_request"
    assert out["target_number"] is None


def test_no_quote_uses_paid_target_budget(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "campaign_config": _cfg(paid_target_budget=500, paid_ceiling=1500),
        **_agency(barter_attempted=True, rate_requested=True),
    })
    assert out["target_number"] == 500


def test_counter_increases_slowly_after_prior_offer(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "prior_proposed_amount": 500,
        "campaign_config": _cfg(
            product_unit_price=2500.0,
            paid_target_budget=500.0,
            paid_ceiling=1500.0,
        ),
        **_direct(barter_attempted=True, rate_requested=True, kol_insists_paid=True),
    })
    assert out["target_number"] == 600


def test_koc_tier_first_counter_uses_half_quote_precise_anchor(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1200,
        "campaign_config": _cfg(paid_target_budget=300.0, paid_ceiling=1500.0),
        "candidate": {"payload": {"follower_count": 40_000}},
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 580
    assert out["rationale_one_line"].startswith("Cash supplement counter")
    assert "creator_tier=koc" in out["rationale_one_line"]
    assert "first single-campaign category test" in out["suggested_wording"]
    assert "quoted rate" not in out["suggested_wording"].lower()


def test_mid_tier_first_counter_uses_vertical_creator_benchmark(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 2000,
        "campaign_config": _cfg(paid_target_budget=500.0, paid_ceiling=2000.0),
        "identity_facts": {"identity.followers": "12万"},
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 1080
    assert "creator_tier=mid_tier" in out["rationale_one_line"]
    assert "first single-campaign category test" in out["suggested_wording"]
    assert "quoted rate" not in out["suggested_wording"].lower()


def test_top_tier_first_counter_uses_higher_ratio_without_round_hundred(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 2000,
        "campaign_config": _cfg(paid_target_budget=500.0, paid_ceiling=2200.0),
        "creator_tier": "top",
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 1180
    assert "creator_tier=top_tier" in out["rationale_one_line"]
    assert "first single-campaign category test" in out["suggested_wording"]
    assert "quoted rate" not in out["suggested_wording"].lower()


def test_reusable_facts_nested_followers_resolve_mid_tier(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 2000,
        "campaign_config": _cfg(paid_ceiling=2000.0),
        "reusable_facts": {"facts": {"followers": 120000}},
        **_agency(barter_attempted=True),
    })
    assert "creator_tier=mid_tier" in out["rationale_one_line"]
    assert out["target_number"] == 1080


def test_tiered_counter_increments_shrink_after_prior_offer(bridge_pkg):
    first = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "prior_proposed_amount": 500,
        "campaign_config": _cfg(paid_target_budget=500.0, paid_ceiling=1500.0),
        "creator_tier": "mid_tier",
        **_agency(barter_attempted=True),
    })
    second = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "prior_proposed_amount": first["target_number"],
        "campaign_config": _cfg(paid_target_budget=500.0, paid_ceiling=1500.0),
        "creator_tier": "mid_tier",
        **_agency(barter_attempted=True),
    })
    third = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 3000,
        "prior_proposed_amount": second["target_number"],
        "campaign_config": _cfg(paid_target_budget=500.0, paid_ceiling=1500.0),
        "creator_tier": "mid_tier",
        **_agency(barter_attempted=True),
    })
    assert first["target_number"] == 650
    assert second["target_number"] == 720
    assert third["target_number"] == 750
    assert second["target_number"] - first["target_number"] == 70
    assert third["target_number"] - second["target_number"] == 30


def test_counter_increase_stays_under_ratio_cap(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1000,
        "prior_proposed_amount": 500,
        "campaign_config": _cfg(
            paid_target_budget=500.0,
            paid_ceiling=1500.0,
        ),
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 500


def test_small_cash_counter_does_not_round_up_to_ten(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 9,
        "campaign_config": _cfg(paid_ceiling=1500),
        **_agency(barter_attempted=True),
    })
    assert out["target_number"] == 4


def test_invalid_mode_raises(bridge_pkg):
    with pytest.raises(bridge_pkg.pricing_engine.PricingInputError):
        _pe(bridge_pkg).compute_offer({"mode": "barter"})


def test_delegated_integrity_resolves_agency(bridge_pkg):
    out = _pe(bridge_pkg).compute_offer({
        "mode": "paid",
        "kol_quoted_amount": 1000,
        "campaign_config": _cfg(),
        "identity_integrity": "delegated",
        "barter_attempted": False,
    })
    assert out["negotiation_phase"] == "paid_counter"
    assert out["target_number"] == 500
