"""Tests for classifier Step 3 fact sanitization."""

from __future__ import annotations


def _cf(bridge_pkg):
    return bridge_pkg.classifier_facts


def test_downgrade_confirmed_on_inquiry_signals(bridge_pkg):
    cf = _cf(bridge_pkg)
    ns, adj = cf.sanitize_classifier_namespaces(
        {"offer": {"offer.interest_signal": "confirmed"}},
        [{"name": "asks_deliverables", "confidence": 0.9}],
    )
    assert ns["offer"]["offer.interest_signal"] == "needs_more_info"
    assert adj


def test_keep_confirmed_with_interest_positive(bridge_pkg):
    cf = _cf(bridge_pkg)
    ns, adj = cf.sanitize_classifier_namespaces(
        {"offer": {"offer.interest_signal": "confirmed"}},
        [{"name": "interest_positive", "confidence": 0.85}],
    )
    assert ns["offer"]["offer.interest_signal"] == "confirmed"
    assert not adj


def test_rewrite_deliverables_on_asks_budget(bridge_pkg):
    cf = _cf(bridge_pkg)
    ns, adj = cf.sanitize_classifier_namespaces(
        {
            "offer": {
                "offer.deliverable_platforms": ["instagram"],
                "offer.deliverable_count_per_platform": {"instagram": 2},
            },
        },
        [{"name": "asks_budget", "confidence": 0.8}],
    )
    assert "offer.deliverable_platforms" not in ns["offer"]
    assert ns["offer"]["offer.deliverable_platforms_proposed"] == ["instagram"]
    assert ns["offer"]["offer.deliverable_count_proposed"] == {"instagram": 2}
    assert any("rewrote" in a for a in adj)


def test_keep_deliverables_when_accepts_terms(bridge_pkg):
    cf = _cf(bridge_pkg)
    ns, adj = cf.sanitize_classifier_namespaces(
        {
            "offer": {
                "offer.deliverable_platforms": ["instagram"],
                "offer.deliverable_count_per_platform": {"instagram": 1},
            },
        },
        [
            {"name": "asks_deliverables", "confidence": 0.7},
            {"name": "accepts_terms", "confidence": 0.9},
        ],
    )
    assert ns["offer"]["offer.deliverable_platforms"] == ["instagram"]
    assert not adj


def test_drop_agreed_terms_without_accepts_terms(bridge_pkg):
    cf = _cf(bridge_pkg)
    ns, adj = cf.sanitize_classifier_namespaces(
        {
            "offer": {
                "offer.compensation_mode": "paid",
                "offer.agreed_terms": "1200 USD flat",
            },
        },
        [{"name": "proposes_rate", "confidence": 0.9}],
    )
    assert "offer.agreed_terms" not in ns["offer"]
    assert ns["offer"]["offer.compensation_mode"] == "paid"
    assert any("agreed_terms" in a for a in adj)


def test_drop_sku_locked_on_oos_inquiry(bridge_pkg):
    cf = _cf(bridge_pkg)
    ns, adj = cf.sanitize_classifier_namespaces(
        {"offer": {"offer.sku_locked": "TS-9999"}},
        [{"name": "requests_oos_sku", "confidence": 0.88}],
    )
    assert "offer.sku_locked" not in ns.get("offer", {})
    assert adj


def test_should_sanitize_email_source(bridge_pkg):
    cf = _cf(bridge_pkg)
    assert cf.should_sanitize_classifier_source("email:abc123")
    assert not cf.should_sanitize_classifier_source("skill:fragment-merge")
