"""Tests for the dispatcher-side helpers ``write_facts_multi`` and the
plugin_api ``get_dispatch_context`` snapshot bundle.

The HTTP route ``get_dispatch_context`` is a thin stitch over four CAL
reads, so we test the four reads return the right shape via the same
``cal`` module the route delegates to. ``write_facts_multi`` is tested
both for the happy path and for atomic pre-validation.
"""

from __future__ import annotations

import pytest


CAMPAIGN = "C-multi-test"


def _seed(cal, *, candidate_payload=None):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN, label="Multi", barter_policy="barter_first",
        product_unit_price=100.0, paid_ceiling=500.0,
        sku_whitelist=["SKU-A"], deliverable_platforms=["instagram"],
        deliverable_count_per_platform=1, contract_required=False,
    )
    iid = cal.upsert_identity(primary_handle="erin", platform="instagram")
    cal.upsert_candidate(
        campaign_id=CAMPAIGN, identity_id=iid, source="discovery",
        candidate_status="selected_for_outreach",
        payload=candidate_payload,
    )
    return iid


def test_write_facts_multi_writes_all_namespaces(cal_db):
    cal = cal_db
    iid = _seed(cal)

    written = cal.write_facts_multi(
        identity_id=iid, campaign_id=CAMPAIGN,
        namespaces={
            "offer": {
                "offer.outreach_sent": True,
                "offer.interest_signal": "confirmed",
            },
            "identity": {"identity.outreach_path": "cold"},
        },
        source="email:m1", env="LIVE",
    )

    assert written == {"offer": 2, "identity": 1}

    facts = cal.latest_facts_for(identity_id=iid, campaign_id=CAMPAIGN)
    assert facts["offer.outreach_sent"] is True
    assert facts["offer.interest_signal"] == "confirmed"
    assert facts["identity.outreach_path"] == "cold"

    # Goal recompute fired: outreach should now be satisfied.
    g = {x["goal"]: x for x in cal.get_goal_state(
        identity_id=iid, campaign_id=CAMPAIGN)}
    assert g["outreach"]["status"] == "satisfied"
    assert g["interest_qualification"]["status"] == "satisfied"


def test_write_facts_multi_rejects_invalid_namespace_atomically(cal_db):
    cal = cal_db
    iid = _seed(cal)

    with pytest.raises(cal.FactNamespaceError):
        cal.write_facts_multi(
            identity_id=iid, campaign_id=CAMPAIGN,
            namespaces={
                "offer": {"offer.outreach_sent": True},
                "bogus": {"bogus.x": 1},
            },
            source="email:m2", env="LIVE",
        )

    # Atomic: nothing should be written from the valid namespace either.
    facts = cal.latest_facts_for(identity_id=iid, campaign_id=CAMPAIGN)
    assert "offer.outreach_sent" not in facts


def test_write_facts_multi_rejects_unprefixed_key(cal_db):
    cal = cal_db
    iid = _seed(cal)

    with pytest.raises(cal.FactNamespaceError):
        cal.write_facts_multi(
            identity_id=iid, campaign_id=CAMPAIGN,
            namespaces={
                "offer": {"outreach_sent": True},  # missing offer. prefix
            },
            source="email:m3", env="LIVE",
        )


def test_dispatch_context_bundle_shape(cal_db):
    """The plugin_api ``/identities/{id}/dispatch-context`` route just
    stitches these five reads. We assert the ingredients are coherent."""
    cal = cal_db
    iid = _seed(cal)
    cal.upsert_relationship(
        identity_id=iid, last_outcome="satisfied", increment_collabs=True,
    )

    goals = cal.get_goal_state(
        identity_id=iid, campaign_id=CAMPAIGN, env="LIVE")
    lanes = cal.get_lanes_view(
        identity_id=iid, campaign_id=CAMPAIGN, env="LIVE")
    rel = cal.get_relationship(iid)
    reusable = cal.get_reusable_facts(iid)
    cfg = cal.get_campaign_config(CAMPAIGN)

    assert isinstance(goals, list) and len(goals) >= 10
    assert set(lanes.keys()) >= {"commerce", "fulfillment", "publish", "meta"}
    assert rel and rel["last_outcome"] == "satisfied"
    assert isinstance(reusable, dict)
    assert cfg and cfg["campaign_id"] == CAMPAIGN


def test_get_candidate_for_returns_payload(cal_db):
    """``get_candidate_for`` is the single-row read used by dispatch-context to
    surface per-campaign discovery evidence (reason, niche_match, showcase)."""
    cal = cal_db
    payload = {
        "reason": "warm hosting reel, family-comfort angle",
        "niche_match": 0.82,
        "showcase_evidence": ["https://instagram.com/p/abc/"],
        "conversion_mechanism": "comfort/movie-night",
    }
    iid = _seed(cal, candidate_payload=payload)

    got = cal.get_candidate_for(
        identity_id=iid, campaign_id=CAMPAIGN, env="LIVE")
    assert got is not None
    assert got["identity_id"] == iid
    assert got["campaign_id"] == CAMPAIGN
    assert got["payload"] == payload
    # ``payload_json`` raw key must not leak through — the consumer reads
    # ``payload`` (dict), not the encoded string.
    assert "payload_json" not in got

    # Wrong campaign / wrong env → None, not a stale fallback.
    assert cal.get_candidate_for(
        identity_id=iid, campaign_id="DOES-NOT-EXIST", env="LIVE") is None
    assert cal.get_candidate_for(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST") is None


def test_dispatch_context_exposes_candidate_payload_and_identity_facts(cal_db):
    """The drafters read ``candidate.payload`` (per-campaign evidence) and
    ``identity_facts`` (creator brief). Verify both surfaces are populated by
    the same reads dispatch-context stitches."""
    cal = cal_db
    payload = {"reason": "honest hosting reviews", "niche_match": 0.74}
    iid = _seed(cal, candidate_payload=payload)

    # Discovery side-effect: persist a creator brief at identity scope
    # (campaign_id=None).
    cal.write_facts_multi(
        identity_id=iid, campaign_id=None,
        namespaces={
            "identity": {
                "identity.content_pillars": ["cozy hosting", "honest reviews"],
                "identity.signature_hooks": ["before/after walk-through"],
                "identity.voice_descriptors": ["warm", "candid"],
                "identity.hero_post_url": "https://instagram.com/p/abc/",
                "identity.hero_post_note": "412k-view comfort-tour reel",
                "identity.recommendation_reason": (
                    "her hosting tours match the family-warmth angle "
                    "we want for the sofa"
                ),
            },
        },
        source="skill:instagram-kol-discovery", env="LIVE",
    )

    # The HTTP route just stitches these; verify the two new reads expose
    # what the route will pass to drafters.
    candidate = cal.get_candidate_for(
        identity_id=iid, campaign_id=CAMPAIGN, env="LIVE")
    identity_facts = cal.latest_facts_for(
        identity_id=iid, campaign_id=None, env="LIVE")

    assert candidate and candidate["payload"]["reason"] == "honest hosting reviews"
    assert identity_facts["identity.content_pillars"] == [
        "cozy hosting", "honest reviews"]
    assert identity_facts["identity.hero_post_url"] == \
        "https://instagram.com/p/abc/"
    assert "cozy hosting" in identity_facts["identity.content_pillars"]
