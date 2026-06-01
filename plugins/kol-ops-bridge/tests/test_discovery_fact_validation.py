"""Validation guardrails for discovery-derived identity facts.

Goal: keep automated writes strict without introducing a human audit step.
The writer must provide well-shaped fact values and include provenance
triples when writing discovery summary fields.
"""

from __future__ import annotations

import pytest


def _seed_identity(cal_db) -> int:
    return cal_db.upsert_identity(
        primary_handle="strict_writer",
        platform="instagram",
        env="TEST",
    )


def test_rejects_hero_post_without_provenance_triple(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.hero_post_url": "https://www.instagram.com/reel/abc123/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    assert "requires provenance keys" in str(exc_info.value)


def test_rejects_non_instagram_hero_post_url(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.hero_post_url": "https://example.com/not-ig",
                "identity.hero_post_url_source": "ig_reel_pick",
                "identity.hero_post_url_discovered_at": "2026-05-29T10:11:12+00:00",
                "identity.hero_post_url_discovered_url": "https://www.instagram.com/strict_writer/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    msg = str(exc_info.value)
    assert "instagram.com" in msg or "instagram reel/post URL" in msg


def test_rejects_indirect_or_tracking_hero_post_url(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.hero_post_url": "https://www.instagram.com/share/reel/ABC123/?igsh=foo",
                "identity.hero_post_url_source": "ig_reel_pick",
                "identity.hero_post_url_discovered_at": "2026-05-29T10:11:12+00:00",
                "identity.hero_post_url_discovered_url": "https://www.instagram.com/strict_writer/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    assert "direct URL" in str(exc_info.value)


def test_rejects_handle_prefixed_hero_post_url(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.hero_post_url": "https://www.instagram.com/techjoyce/reel/DYxeFaAJmEZ/",
                "identity.hero_post_url_source": "ig_reel_pick",
                "identity.hero_post_url_discovered_at": "2026-05-29T10:11:12+00:00",
                "identity.hero_post_url_discovered_url": "https://www.instagram.com/strict_writer/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    assert "canonical /reel/<id>" in str(exc_info.value)


def test_rejects_hero_post_owner_mismatch(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.hero_post_url": "https://www.instagram.com/reel/DYxeFaAJmEZ/",
                "identity.hero_post_url_source": "ig_reel_pick",
                "identity.hero_post_url_discovered_at": "2026-05-29T10:11:12+00:00",
                "identity.hero_post_url_discovered_url": "https://www.instagram.com/another_creator/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    assert "owner mismatch" in str(exc_info.value)


def test_accepts_hero_post_when_owner_matches_identity(cal_db):
    iid = _seed_identity(cal_db)
    cal_db.write_facts(
        identity_id=iid,
        campaign_id=None,
        namespace="identity",
        facts={
            "identity.hero_post_url": "https://www.instagram.com/reel/DYxeFaAJmEZ/",
            "identity.hero_post_url_source": "ig_reel_pick",
            "identity.hero_post_url_discovered_at": "2026-05-29T10:11:12+00:00",
            "identity.hero_post_url_discovered_url": "https://www.instagram.com/strict_writer/",
        },
        source="skill:instagram-kol-discovery",
        env="TEST",
    )
    latest = cal_db.latest_facts_for(identity_id=iid, campaign_id=None, env="TEST")
    assert latest["identity.hero_post_url"] == "https://www.instagram.com/reel/DYxeFaAJmEZ/"


def test_rejects_bad_discovered_at_timestamp(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.instagram_profile_url": "https://www.instagram.com/strict_writer/",
                "identity.instagram_profile_url_source": "ig_bio",
                "identity.instagram_profile_url_discovered_at": "yesterday",
                "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/strict_writer/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    assert "ISO-8601" in str(exc_info.value)


def test_rejects_unsupported_source_enum(cal_db):
    iid = _seed_identity(cal_db)
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=None,
            namespace="identity",
            facts={
                "identity.recommendation_reason": "Strong fit for the cozy home audience.",
                "identity.recommendation_reason_source": "hallucinated_source",
                "identity.recommendation_reason_discovered_at": "2026-05-29T10:11:12+00:00",
                "identity.recommendation_reason_discovered_url": "https://www.instagram.com/strict_writer/",
            },
            source="skill:instagram-kol-discovery",
            env="TEST",
        )
    msg = str(exc_info.value)
    assert "unsupported source value" in msg
    assert "hallucinated_source" in msg
    assert "allowed values" in msg


def test_accepts_well_formed_incremental_candidate_write(cal_db):
    iid = _seed_identity(cal_db)
    cal_db.write_facts(
        identity_id=iid,
        campaign_id=None,
        namespace="identity",
        facts={
            "identity.instagram_profile_url": "https://www.instagram.com/strict_writer/",
            "identity.instagram_profile_url_source": "ig_bio",
            "identity.instagram_profile_url_discovered_at": "2026-05-29T10:11:12+00:00",
            "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/strict_writer/",
            "identity.content_pillars": ["cozy living", "family routines"],
            "identity.content_pillars_source": "ig_profile_and_reels",
            "identity.content_pillars_discovered_at": "2026-05-29T10:11:12+00:00",
            "identity.content_pillars_discovered_url": "https://www.instagram.com/strict_writer/",
        },
        source="skill:instagram-kol-discovery",
        env="TEST",
    )
    latest = cal_db.latest_facts_for(identity_id=iid, campaign_id=None, env="TEST")
    assert latest["identity.instagram_profile_url"] == "https://www.instagram.com/strict_writer/"
    assert latest["identity.content_pillars"] == ["cozy living", "family routines"]
