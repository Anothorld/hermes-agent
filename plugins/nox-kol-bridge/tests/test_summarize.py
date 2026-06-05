"""Tests for normalized_summary field extraction."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.summarize import summarize_diligence_pack  # noqa: E402


def test_summarize_maps_avg_views_and_engagement():
    bundle = {
        "profile": {
            "success": True,
            "data": {
                "creator_id": "abc",
                "creator_name": "Test",
                "avg_views": 1000,
                "engagement_rate": 0.05,
                "channel_url": "https://youtube.com/c/x",
            },
        },
        "audience": {
            "success": True,
            "data": {"audience_authenticity": "healthy"},
        },
        "content": {"success": True, "data": {}},
    }
    out = summarize_diligence_pack(bundle)
    assert out["nox_creator_id"] == "abc"
    assert out["avg_views"] == 1000
    assert out["engagement_rate"] == 0.05
    assert out["highlights"]["audience_authenticity"] == "healthy"


def test_summarize_unwraps_audience_authenticity_object():
    bundle = {
        "profile": {"success": True, "data": {"creator_id": "x"}},
        "audience": {
            "success": True,
            "data": {
                "audience_authenticity": {
                    "value": 0.838,
                    "status": 4,
                    "ratio_min": 0.7,
                    "ratio_max": 0.9,
                },
                "audience_quality": {"value": 72, "status": 3},
            },
        },
        "content": {"success": True, "data": {}},
    }
    out = summarize_diligence_pack(bundle)
    assert out["audience_authenticity"] == 0.838
    assert out["audience_quality_score"] == 72


def test_summarize_parses_instagram_genders_and_regions_with_value_key():
    bundle = {
        "profile": {"success": True, "data": {"creator_id": "ig-1"}},
        "audience": {
            "success": True,
            "data": {
                "regions": [
                    {"name": "US", "value": 0.741},
                    {"name": "CA", "value": 0.051},
                ],
                "genders": [
                    {"name": "female", "value": 0.671},
                    {"name": "male", "value": 0.329},
                ],
                "female_ages": [{"name": "25-34", "value": 0.129}],
                "male_ages": [{"name": "25-34", "value": 0.063}],
                "languages": [{"name": "en", "value": 1}],
                "audience_types": [
                    {"name": "usualUser", "value": 0.739},
                    {"name": "suspicious", "value": 0.154},
                ],
                "adults": [
                    {"name": "children", "value": 0.066},
                    {"name": "adults", "value": 0.934},
                ],
                "positive_audience_pct": 61,
                "promo_attractiveness": 0,
            },
        },
        "content": {"success": True, "data": {}},
    }
    out = summarize_diligence_pack(bundle)
    assert out["audience_top_regions"] == "US (74.1%), CA (5.1%)"
    assert out["gender_skew"] == "female (67.1%), male (32.9%)"
    assert "女 25-34 (12.9%)" in out["audience_age_distribution"]
    assert "男 25-34 (6.3%)" in out["audience_age_distribution"]
    assert out["audience_languages_top"] == "en (100.0%)"
    assert "真实用户 (73.9%)" in out["audience_types_top"]
    assert out["audience_adults_split"] == "children (6.6%), adults (93.4%)"
    assert out["audience_positive_pct"] == 61
    assert out["audience_promo_attractiveness"] == 0


def test_summarize_maps_content_audience_interests_and_profile_performance():
    bundle = {
        "profile": {
            "success": True,
            "data": {
                "creator_id": "ig-2",
                "channel_handle": "home_creator",
                "social_media": [{"platform": "instagram", "url": "https://instagram.com/home_creator"}],
                "median_views": 210062,
                "wave": 0.6898,
                "avg_active_days": 2,
                "view_per_followers": 0.4628,
                "avg_views_level": 2,
                "wave_level": 3,
                "engagement_rate_level": 3,
                "posts_count": 10,
                "reels_count": 5,
                "avg_engagement_reels": 430,
                "avg_views_benchmark": {"rank": 0.75},
                "engagement_rate_benchmark": {"rank": 0.39},
                "cooperation_score": 0,
                "cooperation_pros": ["responsive"],
                "cooperation_cons": [],
            },
        },
        "audience": {
            "success": True,
            "data": {
                "audience_authenticity": {
                    "value": 0.838,
                    "ratio_min": 0.7905,
                    "ratio_max": 0.8282,
                },
            },
        },
        "content": {
            "success": True,
            "data": {
                "top_tags": ["home", "collab"],
                "all_tags": ["home", "collab", "gift", "beauty"],
                "audience_interests": [
                    {"keyword": "Home & Lifestyle Products", "description": "..."},
                ],
            },
        },
    }
    out = summarize_diligence_pack(bundle)
    assert out["platform"] == "instagram"
    assert out["channel_handle"] == "home_creator"
    assert out["median_views"] == 210062
    assert out["wave"] == 0.6898
    assert "播放 L2" in out["performance_levels"]
    assert "播放 75%" in out["benchmark_ranks"]
    assert "Home & Lifestyle Products" in out["audience_interests_top"]
    assert out["content_tags_all"] == "home, collab, gift, beauty"
    assert "帖 10" in out["content_format_counts"]
    assert "Reels 430" in out["content_engagement_split"]
    assert out["cooperation_score"] == 0
    assert out["cooperation_pros"] == "responsive"
    assert out["audience_authenticity_range"] == "79.0%–82.8%"


def test_summarize_maps_cooperation_commercial_fields():
    bundle = {
        "profile": {"success": True, "data": {"creator_id": "yt-1"}},
        "audience": {"success": True, "data": {}},
        "content": {"success": True, "data": {}},
        "cooperation": {
            "success": True,
            "data": {
                "estimated_price_min": 3300,
                "estimated_price_max": 5200,
                "estimated_price_avg": 4250,
                "first_price_min": 5200,
                "first_price_max": 8700,
                "final_price_min": 3300,
                "final_price_max": 5200,
                "avg_response_hours": 14,
                "avg_contact_days": 7.1,
                "avg_contact_chats": 6.7,
                "avg_collaboration_days": 35.2,
                "ad_video_percent": 0.0073,
                "ad_video_count_per_month": 0.4,
                "ad_video_avg_views": 182675,
                "brands": [
                    {
                        "brand_name": "Microsoft",
                        "video_count": 7,
                        "engagement_rate": 0.0129,
                    },
                ],
                "cooperation_confirmation_pct": 0.0553,
                "start_contact_pct": 0.0174,
                "promotion_online_pct": 0.9739,
                "active_period_min": "22:30:00",
                "active_period_max": "02:00:00",
                "brand_video_engagement_rate": 0.0182,
            },
        },
    }
    out = summarize_diligence_pack(bundle)
    assert "$3,300" in out["cooperation_price_estimate"]
    assert "$5,200" in out["cooperation_first_price_range"]
    assert out["cooperation_avg_response_hours"] == 14
    assert "联系 7.1" in out["cooperation_contact_efficiency"]
    assert "Microsoft" in out["cooperation_brands_top"]
    assert out["cooperation_confirmation_pct"] == 0.0553
    assert out["cooperation_active_period"] == "22:30:00–02:00:00"


def test_verdict_honors_merged_dispute_count_from_profile():
    bundle = {
        "profile": {
            "success": True,
            "data": {
                "creator_id": "x",
                "engagement_rate": 0.05,
                "avg_views": 1000,
                "dispute_types": ["late_delivery"],
            },
        },
        "audience": {
            "success": True,
            "data": {"audience_authenticity": {"value": 0.9, "status": 4}},
        },
        "content": {"success": True, "data": {"engagement_rate": 0.05}},
        "cooperation": {"success": True, "data": {}},
    }
    out = summarize_diligence_pack(bundle)
    assert out["nox_diligence_verdict"] == "not_priority"
    assert out["dispute_count"] == 1


def test_default_dimensions_include_cooperation_api_calls(nox_home):
    from internal import commands  # noqa: PLC0415

    out = commands.cmd_diligence_pack(
        env="TEST",
        gate="shortlist_confirm",
        monthly_budget=50,
        tz_name="UTC",
        lang="en",
        nox_creator_id="gate_a_default_dims_unique",
        platform=None,
        url=None,
        channel_id=None,
        dimensions=[],
        include_cooperation=False,
    )
    assert out["cache_hit"] is False
    assert out["api_calls"] == 4
    assert "cooperation" in out["cache_key"]


def test_summarize_unwraps_nox_score_object():
    bundle = {
        "profile": {
            "success": True,
            "data": {
                "creator_id": "x",
                "nox_score": {
                    "overall": 68,
                    "growth": 70,
                    "creativity": 65,
                    "audience": 72,
                    "engagement": 60,
                    "credibility": 71,
                },
            },
        },
        "audience": {"success": True, "data": {}},
        "content": {"success": True, "data": {}},
    }
    out = summarize_diligence_pack(bundle)
    assert out["nox_score"] == 68
    assert out["nox_score_breakdown"] == {
        "overall": 68,
        "growth": 70,
        "creativity": 65,
        "audience": 72,
        "engagement": 60,
        "credibility": 71,
    }
