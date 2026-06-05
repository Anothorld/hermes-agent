"""CAL validators for Console profile OG cache facts."""

from __future__ import annotations

import datetime as dt


def test_write_profile_og_facts(cal_db):
    iid = cal_db.upsert_identity(primary_handle="og_cache_kol", platform="instagram")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    n = cal_db.write_facts(
        identity_id=iid,
        campaign_id=None,
        namespace="identity",
        facts={
            "identity.profile_og_source_url": "https://www.instagram.com/og_cache_kol/",
            "identity.profile_og_fetched_at": now,
            "identity.profile_og_image_url": "https://cdninstagram.com/x.jpg",
            "identity.profile_og_title": "OG title",
            "identity.profile_og_description": "1M Followers",
        },
        source="console:profile_og_cache",
        env="LIVE",
    )
    assert n == 5
