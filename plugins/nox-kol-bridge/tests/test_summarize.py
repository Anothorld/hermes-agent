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
