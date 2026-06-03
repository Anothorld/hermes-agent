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
