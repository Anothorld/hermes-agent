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


def test_cover_mode_returns_two_covers_no_videos():
    reels = [_reel(i, views=i * 10_000) for i in range(15)]
    plan = build_content_eval_plan(reels, eval_mode="cover", handle="testkol")

    assert plan["eval_mode"] == "cover"
    assert plan["videos_target"] == 0
    assert len(plan["cover_reels"]) == 2
    assert plan["video_reels"] == []
    assert plan["cover_reels"][0]["reel_id"] == "reel0"
    assert plan["selection"]["video_selection"] == "covers_only"


def test_video_mode_random_three_from_recent_ten():
    reels = [_reel(i) for i in range(12)]
    plan = build_content_eval_plan(reels, eval_mode="video", handle="sampleuser", seed=42)

    assert plan["eval_mode"] == "video"
    assert plan["videos_target"] == 3
    assert len(plan["cover_reels"]) == 2
    assert len(plan["video_reels"]) == 3
    # Videos sampled from recent-10 pool, not limited to the 2 cover slots
    cover_ids = {r["reel_id"] for r in plan["cover_reels"]}
    video_ids = {r["reel_id"] for r in plan["video_reels"]}
    assert video_ids.issubset({f"reel{i}" for i in range(10)})
    assert plan["selection"]["video_selection"] == "random_3_from_recent_10"
    # At least one video may be outside the 2-cover set
    assert len(video_ids | cover_ids) >= 2


def test_video_mode_deterministic_per_handle():
    reels = [_reel(i) for i in range(10)]
    a = build_content_eval_plan(reels, eval_mode="video", handle="samehandle")
    b = build_content_eval_plan(reels, eval_mode="video", handle="samehandle")
    assert [r["reel_id"] for r in a["video_reels"]] == [r["reel_id"] for r in b["video_reels"]]


def test_fewer_than_cover_count_still_builds_plan():
    reels = [_reel(0)]
    plan = build_content_eval_plan(reels, eval_mode="video", handle="tiny", seed=1)

    assert len(plan["cover_reels"]) == 1
    assert len(plan["video_reels"]) == 1
    assert plan["selection"]["pool_size"] == 1
