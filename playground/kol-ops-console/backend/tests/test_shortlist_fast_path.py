"""Shortlist fast-path enrichment (no live OG / per-row read_facts)."""

from __future__ import annotations

from app.routers.campaigns import _merge_prior_outreach_touch
from app.shortlist_profile_og import attach_cached_link_previews


def test_attach_cached_link_previews_uses_cal_facts_only():
    candidates = [{
        "handle": "creator",
        "platform": "instagram",
        "preview_facts": {
            "identity.instagram_profile_url": "https://www.instagram.com/creator/",
            "identity.profile_og_title": "Creator Title",
            "identity.profile_og_source_url": "https://www.instagram.com/creator/",
            "identity.profile_og_fetched_at": "2026-06-05T10:00:00Z",
        },
    }]
    attach_cached_link_previews(candidates)
    row = candidates[0]
    assert row["social_links"]
    assert row["link_previews"]["https://www.instagram.com/creator/"]["title"] == "Creator Title"


def test_merge_prior_outreach_touch_prefers_newer_campaign_fact():
    merged = _merge_prior_outreach_touch(
        campaign_id="C1",
        camp_facts={"offer.outreach_sent_at": "2026-06-05T12:00:00Z"},
        touch_from_batch={
            "last_touch_at": "2026-01-01T00:00:00Z",
            "last_touch_campaign_id": "OLD",
        },
    )
    assert merged is not None
    assert merged["last_touch_at"] == "2026-06-05T12:00:00Z"
