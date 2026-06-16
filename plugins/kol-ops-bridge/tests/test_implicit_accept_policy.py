"""Tests for deterministic implicit-accept policy."""

from __future__ import annotations


def _iap(bridge_pkg):
    return bridge_pkg.implicit_accept_policy


def _base_state() -> dict:
    return {
        "offer.deliverable_platforms": ["instagram", "tiktok"],
        "offer.deliverable_count_per_platform": 1,
        "offer.usage_rights_discussed": True,
        "offer.last_outbound_terms_proposed": (
            "We need 1 short video cross-posted on IG/TikTok/YT Shorts plus ad code."
        ),
    }


def _base_cfg() -> dict:
    return {
        "implicit_accept_enabled": True,
        "defer_terms_to_contract": True,
        "strict_explicit_accept": False,
        "default_compensation_mode": "gifted",
        "product_unit_price": 2599.0,
    }


def test_implicit_accept_megan_like(bridge_pkg):
    iap = _iap(bridge_pkg)
    signals = [{"name": "interest_positive", "confidence": 0.9}]
    thread_meta = {"outbound_bodies": []}
    assert iap.should_apply_implicit_accept(
        state=_base_state(),
        signals=signals,
        campaign_cfg=_base_cfg(),
        thread_meta=thread_meta,
        incoming_offer={},
    )
    merged, adj, audit = iap.merge_policy_facts(
        {"offer": {"offer.interest_signal": "confirmed"}},
        state=_base_state(),
        signals=signals,
        campaign_cfg=_base_cfg(),
        thread_meta=thread_meta,
        source="email:19eb27577dd1ea31",
    )
    assert merged["offer"]["offer.compensation_mode"] == "gifted"
    assert merged["offer"]["offer.agreed_terms"]["source"] == "policy:implicit_accept"
    assert adj
    assert audit is not None


def test_block_paid_only_stance(bridge_pkg):
    iap = _iap(bridge_pkg)
    signals = [{"name": "paid_only_stance", "confidence": 0.9}]
    assert not iap.should_apply_implicit_accept(
        state=_base_state(),
        signals=signals,
        campaign_cfg=_base_cfg(),
        thread_meta={"outbound_bodies": []},
        incoming_offer={},
    )


def test_block_proposes_rate(bridge_pkg):
    iap = _iap(bridge_pkg)
    signals = [{"name": "proposes_rate", "confidence": 0.85}]
    assert not iap.should_apply_implicit_accept(
        state=_base_state(),
        signals=signals,
        campaign_cfg=_base_cfg(),
        thread_meta={"outbound_bodies": []},
        incoming_offer={},
    )


def test_strict_explicit_accept_disables(bridge_pkg):
    iap = _iap(bridge_pkg)
    cfg = dict(_base_cfg(), strict_explicit_accept=True)
    signals = [{"name": "interest_positive", "confidence": 0.9}]
    assert not iap.should_apply_implicit_accept(
        state=_base_state(),
        signals=signals,
        campaign_cfg=cfg,
        thread_meta={"outbound_bodies": []},
        incoming_offer={},
    )


def test_brand_terms_from_outbound_bodies(bridge_pkg):
    iap = _iap(bridge_pkg)
    state = dict(_base_state())
    state.pop("offer.last_outbound_terms_proposed", None)
    bodies = [
        "For this collab we need 1 reel on instagram and tiktok with ad code.",
    ]
    assert iap.brand_proposed_terms(state, {"outbound_bodies": bodies})


def test_contract_signed_backfill(bridge_pkg):
    iap = _iap(bridge_pkg)
    merged, adj, _ = iap.merge_policy_facts(
        {"offer": {"offer.contract_signed": True}},
        state=_base_state(),
        signals=[],
        campaign_cfg=_base_cfg(),
        thread_meta={},
        source="skill:kol-contract-coordinator",
    )
    assert merged["offer"]["offer.agreed_terms"]["source"] == "contract_signed_snapshot"
    assert any("contract_signed" in a for a in adj)


def test_gifted_compensation_satisfied_without_agreed_terms(bridge_pkg):
    goals = bridge_pkg.goals
    ctx = goals.Context(
        campaign_cfg=_base_cfg(),
        relationship={},
        is_repeat_kol=False,
    )
    state = {
        "offer.compensation_mode": "gifted",
    }
    assert goals._compensation_satisfied(state, ctx)


def test_paid_still_requires_agreed_terms(bridge_pkg):
    goals = bridge_pkg.goals
    ctx = goals.Context(campaign_cfg=_base_cfg(), relationship={}, is_repeat_kol=False)
    state = {"offer.compensation_mode": "paid"}
    assert not goals._compensation_satisfied(state, ctx)


def test_paid_mode_blocks_implicit_agreed_terms(bridge_pkg):
    iap = _iap(bridge_pkg)
    state = dict(_base_state(), **{"offer.compensation_mode": "paid"})
    signals = [{"name": "continues_without_objection", "confidence": 0.9}]
    merged, adj, audit = iap.merge_policy_facts(
        {"offer": {"offer.interest_signal": "confirmed"}},
        state=state,
        signals=signals,
        campaign_cfg=_base_cfg(),
        thread_meta={"outbound_bodies": []},
        source="email:test-paid-block",
    )
    assert "offer.agreed_terms" not in merged.get("offer", {})
    assert audit is None


def test_escalation_blocks_implicit_accept(bridge_pkg):
    iap = _iap(bridge_pkg)
    signals = [{"name": "interest_positive", "confidence": 0.9}]
    blocked = not iap.should_apply_implicit_accept(
        state=_base_state(),
        signals=signals,
        campaign_cfg=_base_cfg(),
        thread_meta={"outbound_bodies": []},
        incoming_offer={},
        goal_snapshot={
            "compensation_negotiation": {"blocking_escalation_id": 42},
        },
    )
    assert blocked


def test_brand_proposed_terms_rejects_generic_outbound(bridge_pkg):
    iap = _iap(bridge_pkg)
    state = dict(_base_state())
    state["offer.last_outbound_terms_proposed"] = "Thanks — looking forward to it!"
    assert not iap.brand_proposed_terms(state, {"outbound_bodies": []}, campaign_cfg=_base_cfg())


def test_inquiry_only_without_brand_terms(bridge_pkg):
    iap = _iap(bridge_pkg)
    active = {"asks_budget"}
    state = dict(_base_state())
    state.pop("offer.last_outbound_terms_proposed", None)
    assert iap._inquiry_only_without_brand_terms(
        active,
        state,
        {"outbound_bodies": []},
        campaign_cfg=_base_cfg(),
    )
