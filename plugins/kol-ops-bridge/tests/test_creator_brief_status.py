"""Tests for creator brief readiness assessment."""

from __future__ import annotations

import datetime as dt


def _full_brief_facts(*, discovered_at: str) -> dict:
    return {
        "identity.content_pillars": ["cozy living", "family routines"],
        "identity.signature_hooks": ["before/after tour"],
        "identity.voice_descriptors": ["warm", "honest"],
        "identity.hero_post_url": "https://www.instagram.com/reel/abc123/",
        "identity.hero_post_note": "412k-view comfort tour",
        "identity.recommendation_reason": "Strong fit for family sofa campaign.",
        "identity.content_pillars_discovered_at": discovered_at,
    }


def test_assess_ready_when_fresh(bridge_pkg) -> None:
    cbs = bridge_pkg.creator_brief_status
    now = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    out = cbs.assess_creator_brief_readiness(
        _full_brief_facts(discovered_at="2026-05-01T12:00:00Z"),
        now=now,
    )
    assert out["ready"] is True
    assert out["status"] == "ready"
    assert out["missing_keys"] == []
    assert out["stale"] is False


def test_assess_stale_when_anchor_old(bridge_pkg) -> None:
    cbs = bridge_pkg.creator_brief_status
    now = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    out = cbs.assess_creator_brief_readiness(
        _full_brief_facts(discovered_at="2025-01-01T00:00:00Z"),
        now=now,
    )
    assert out["ready"] is False
    assert out["status"] == "stale"
    assert out["stale"] is True


def test_assess_missing_keys(bridge_pkg) -> None:
    cbs = bridge_pkg.creator_brief_status
    facts = _full_brief_facts(discovered_at="2026-05-01T12:00:00Z")
    del facts["identity.hero_post_url"]
    out = cbs.assess_creator_brief_readiness(facts)
    assert out["ready"] is False
    assert out["status"] == "missing"
    assert "identity.hero_post_url" in out["missing_keys"]


def test_validate_bundle_all_or_nothing(bridge_pkg) -> None:
    cbs = bridge_pkg.creator_brief_status
    assert cbs.validate_creator_brief_bundle({}) == []
    partial = {"identity.content_pillars": ["cozy"]}
    assert "identity.signature_hooks" in cbs.validate_creator_brief_bundle(partial)
