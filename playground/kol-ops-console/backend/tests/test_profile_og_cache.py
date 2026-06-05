"""Tests for CAL-backed profile OG cache."""

import datetime as dt

from app.profile_og_cache import (
    facts_from_link_preview,
    link_preview_from_facts,
    normalize_profile_url,
    og_cache_is_fresh,
)


def test_normalize_profile_url_trailing_slash():
    a = normalize_profile_url("https://www.instagram.com/foo/")
    b = normalize_profile_url("https://www.instagram.com/foo")
    assert a == b


def test_og_cache_fresh_within_ttl():
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    facts = {
        "identity.profile_og_source_url": "https://www.instagram.com/kol/",
        "identity.profile_og_fetched_at": now,
        "identity.profile_og_image_url": "https://cdn.example/a.jpg",
        "identity.profile_og_title": "KOL (@kol)",
    }
    assert og_cache_is_fresh(facts, "https://www.instagram.com/kol")


def test_og_cache_stale_when_url_mismatch():
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    facts = {
        "identity.profile_og_source_url": "https://www.instagram.com/other/",
        "identity.profile_og_fetched_at": now,
        "identity.profile_og_title": "x",
    }
    assert not og_cache_is_fresh(facts, "https://www.instagram.com/kol")


def test_link_preview_from_facts_shape():
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    facts = {
        "identity.profile_og_source_url": "https://www.instagram.com/kol/",
        "identity.profile_og_fetched_at": now,
        "identity.profile_og_image_url": "https://cdn.example/a.jpg",
        "identity.profile_og_title": "Title",
        "identity.profile_og_description": "Desc",
    }
    out = link_preview_from_facts(facts, "https://www.instagram.com/kol")
    assert out is not None
    assert out["ok"] is True
    assert out["source"] == "cal_cache"
    assert out["image"] == "https://cdn.example/a.jpg"


def test_facts_from_link_preview():
    facts = facts_from_link_preview(
        "https://www.instagram.com/kol/",
        {"ok": True, "title": "T", "description": "D", "image": "https://cdn/x.jpg"},
    )
    assert facts["identity.profile_og_source_url"] == "https://www.instagram.com/kol/"
    assert facts["identity.profile_og_title"] == "T"
    assert "identity.profile_og_fetched_at" in facts
