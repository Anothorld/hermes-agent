"""Tests for profile URL resolution helpers."""

from app.kol_profile_url import guess_profile_url, resolve_profile_url


def test_guess_instagram():
    assert guess_profile_url("instagram", "MyKol") == "https://www.instagram.com/MyKol/"


def test_guess_tiktok():
    assert guess_profile_url("tiktok", "@kol") == "https://www.tiktok.com/@kol"


def test_resolve_prefers_cal_fact():
    url = resolve_profile_url(
        platform="instagram",
        handle="kol",
        facts={"identity.tiktok_profile_url": "https://www.tiktok.com/@real"},
    )
    assert url == "https://www.tiktok.com/@real"


def test_resolve_platform_fact_before_guess():
    url = resolve_profile_url(
        platform="youtube",
        handle="kol",
        facts={"identity.youtube_profile_url": "https://www.youtube.com/@kol"},
    )
    assert url == "https://www.youtube.com/@kol"
