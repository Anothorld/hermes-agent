"""Tests for content_eval reel selection plan."""

from __future__ import annotations

import sys
from pathlib import Path

_INTERNAL = Path(__file__).resolve().parents[1] / "internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

from content_eval import build_content_eval_plan  # noqa: E402


def _reel(n: int, views: int = 100_000) -> dict:
    rid = f"reel{n}"
    return {
        "reel_id": rid,
        "url": f"https://www.instagram.com/reel/{rid}/",
        "thumbnail_url": f"https://cdn.example/{rid}.jpg",
        "views": views,
    }


def test_text_mode_caption_comments_only():
    reels = [_reel(i) for i in range(12)]
    plan = build_content_eval_plan(reels, eval_mode="text", handle="textkol")

    assert plan["eval_mode"] == "text"
    assert plan["videos_target"] == 0
    assert len(plan["cover_reels"]) == 10
    assert plan["video_reels"] == []
    assert plan["selection"]["video_selection"] == "caption_and_comments_only"
    assert plan["selection"]["screening_basis"] == "caption_and_comments"


def test_cover_mode_returns_ten_covers_no_videos():
    reels = [_reel(i, views=i * 10_000) for i in range(15)]
    plan = build_content_eval_plan(reels, eval_mode="cover", handle="testkol")

    assert plan["eval_mode"] == "cover"
    assert plan["videos_target"] == 0
    assert len(plan["cover_reels"]) == 10
    assert plan["video_reels"] == []
    assert plan["cover_reels"][0]["reel_id"] == "reel0"
    assert plan["selection"]["video_selection"] == "covers_only"


def test_video_mode_random_three_from_recent_ten():
    reels = [_reel(i) for i in range(12)]
    plan = build_content_eval_plan(reels, eval_mode="video", handle="sampleuser", seed=42)

    assert plan["eval_mode"] == "video"
    assert plan["videos_target"] == 3
    assert len(plan["cover_reels"]) == 10
    assert len(plan["video_reels"]) == 3
    cover_ids = {r["reel_id"] for r in plan["cover_reels"]}
    for picked in plan["video_reels"]:
        assert picked["reel_id"] in cover_ids
    assert plan["selection"]["video_selection"] == "random_3_from_recent_10"


def test_video_mode_deterministic_per_handle():
    reels = [_reel(i) for i in range(10)]
    a = build_content_eval_plan(reels, eval_mode="video", handle="samehandle")
    b = build_content_eval_plan(reels, eval_mode="video", handle="samehandle")
    assert [r["reel_id"] for r in a["video_reels"]] == [r["reel_id"] for r in b["video_reels"]]


def test_fewer_than_ten_reels_still_builds_plan():
    reels = [_reel(0), _reel(1)]
    plan = build_content_eval_plan(reels, eval_mode="video", handle="tiny", seed=1)

    assert len(plan["cover_reels"]) == 2
    assert len(plan["video_reels"]) == 2
    assert plan["selection"]["pool_size"] == 2
