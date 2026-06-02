"""Tests for fragment-mode fact ownership (assert_disjoint)."""

from __future__ import annotations

import pytest


def _dr(bridge_pkg):
    return bridge_pkg.dispatch_router


def test_assert_disjoint_merges_non_overlapping(bridge_pkg):
    dr = _dr(bridge_pkg)
    merged = dr.assert_disjoint({
        "product_selection": {"offer.proposed_skus": ["v1"]},
        "deliverables_scope": {
            "offer.deliverable_platforms_proposed": ["instagram"],
            "offer.deliverable_count_proposed": 1,
            "offer.usage_rights_discussed": True,
        },
    })
    assert merged["offer.proposed_skus"] == ["v1"]
    assert merged["offer.deliverable_platforms_proposed"] == ["instagram"]


def test_assert_disjoint_rejects_committed_deliverables_from_fragment(bridge_pkg):
    """Committed scope keys must not be proposed from fragment mode."""
    dr = _dr(bridge_pkg)
    with pytest.raises(dr.FactOwnershipError) as exc:
        dr.assert_disjoint({
            "deliverables_scope": {"offer.deliverable_platforms": ["instagram"]},
        })
    assert any("not owned" in c for c in exc.value.conflicts)


def test_assert_disjoint_rejects_interest_signal_from_fragment(bridge_pkg):
    dr = _dr(bridge_pkg)
    with pytest.raises(dr.FactOwnershipError) as exc:
        dr.assert_disjoint({
            "interest_qualification": {"offer.interest_signal": "confirmed"},
        })
    assert any("not owned" in c for c in exc.value.conflicts)


def test_assert_disjoint_rejects_agreed_terms_from_fragment(bridge_pkg):
    dr = _dr(bridge_pkg)
    with pytest.raises(dr.FactOwnershipError) as exc:
        dr.assert_disjoint({
            "compensation_negotiation": {
                "offer.compensation_mode": "paid",
                "offer.agreed_terms": "flat 1200 USD",
            },
        })
    assert any("not owned" in c for c in exc.value.conflicts)


def test_assert_disjoint_rejects_duplicate_key(bridge_pkg):
    dr = _dr(bridge_pkg)
    with pytest.raises(dr.FactOwnershipError) as exc:
        dr.assert_disjoint({
            "product_selection": {"offer.sku_locked": "v1"},
            "deliverables_scope": {"offer.sku_locked": "v2"},
        })
    assert any("multiple goals" in c for c in exc.value.conflicts)


def test_assert_disjoint_rejects_foreign_key(bridge_pkg):
    dr = _dr(bridge_pkg)
    with pytest.raises(dr.FactOwnershipError) as exc:
        dr.assert_disjoint({
            "interest_qualification": {"offer.sku_locked": "v1"},
        })
    assert any("not owned" in c for c in exc.value.conflicts)
