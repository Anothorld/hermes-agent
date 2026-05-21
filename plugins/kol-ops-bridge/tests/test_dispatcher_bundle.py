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


def _seed(cal):
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
    stitches these four reads. We assert the ingredients are coherent."""
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

    assert isinstance(goals, list) and len(goals) >= 10
    assert set(lanes.keys()) >= {"commerce", "fulfillment", "publish", "meta"}
    assert rel and rel["last_outcome"] == "satisfied"
    assert isinstance(reusable, dict)
