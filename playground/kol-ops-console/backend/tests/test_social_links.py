"""Tests for shortlist social link listing."""

from app.kol_profile_url import list_social_links_for_candidate


def test_list_social_links_from_facts():
    links = list_social_links_for_candidate(
        facts={
            "identity.instagram_profile_url": "https://www.instagram.com/a/",
            "identity.tiktok_profile_url": "https://www.tiktok.com/@a",
        },
        platform="instagram",
        handle="a",
    )
    assert len(links) == 2
    assert links[0]["short_label"] == "IG"


def test_list_social_links_multiple_from_facts():
    links = list_social_links_for_candidate(
        facts={
            "identity.instagram_profile_url": "https://www.instagram.com/a/",
            "identity.personal_site_url": "https://studio.example.com",
        },
        platform="instagram",
        handle="a",
    )
    assert len(links) == 2
    assert links[1]["short_label"] == "site"


def test_list_social_links_inferred_without_facts():
    links = list_social_links_for_candidate(
        facts={},
        platform="tiktok",
        handle="kol",
    )
    assert len(links) == 1
    assert links[0]["url"] == "https://www.tiktok.com/@kol"
