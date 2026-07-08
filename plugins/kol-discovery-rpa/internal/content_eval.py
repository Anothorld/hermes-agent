"""Content-evaluation reel selection — cover pool + random video sample.

After ``fetch_reels`` extracts the profile /reels/ grid, this module builds
the structured plan that downstream download + vision tools consume:

- **cover_reels**: first N reels from the profile grid (default 10), each with
  ``thumbnail_url`` scraped by RPA from the grid ``img[src]``.
- **video_reels**: when video eval is ON, a random sample of M reels (default 3)
  from that same recent-N pool — not top-by-views.

Selection is deterministic per handle (seeded RNG) so re-runs on the same
candidate pick the same video sample unless the reel list changes.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from eval_mode import EVAL_COVER_COUNT, EVAL_VIDEO_COUNT, resolve_eval_mode


def _seed_for_handle(handle: str) -> int:
    digest = hashlib.sha256(handle.lstrip("@").lower().encode()).hexdigest()
    return int(digest[:16], 16)


def build_content_eval_plan(
    reels: list[dict[str, Any]],
    *,
    eval_mode: str | None = None,
    cover_count: int = EVAL_COVER_COUNT,
    video_count: int = EVAL_VIDEO_COUNT,
    handle: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    """Build the cover + video selection plan from a reels grid extract.

    Args:
        reels: Reel dicts from ``fetch_reels`` (order = profile grid, most
            recent first).
        eval_mode: ``"cover"`` or ``"video"``. Resolved from env/brief when
            omitted.
        cover_count: How many recent reels to use for cover screening.
        video_count: How many reels to randomly sample for video deep-eval
            when ``eval_mode == "video"``.
        handle: IG handle — used for deterministic random seed when ``seed``
            is omitted.
        seed: Optional explicit RNG seed (tests).

    Returns:
        Dict with ``eval_mode``, targets, ``cover_reels``, ``video_reels``,
        and ``selection`` metadata for ingest payloads.
    """
    mode = eval_mode or resolve_eval_mode()
    pool = list(reels[:cover_count])
    cover_reels = [_slim_reel(r) for r in pool if r.get("url")]

    video_reels: list[dict[str, Any]] = []
    selection_note = "covers_only"
    if mode == "video" and pool:
        rng = random.Random(seed if seed is not None else _seed_for_handle(handle))
        k = min(video_count, len(pool))
        if k > 0:
            picked = rng.sample(pool, k)
            video_reels = [_slim_reel(r) for r in picked]
            selection_note = f"random_{k}_from_recent_{len(pool)}"

    return {
        "eval_mode": mode,
        "covers_target": cover_count,
        "videos_target": video_count if mode == "video" else 0,
        "cover_reels": cover_reels,
        "video_reels": video_reels,
        "selection": {
            "cover_source": "profile_reels_grid_first_n",
            "video_source": "random_from_recent_pool" if mode == "video" else None,
            "video_selection": selection_note,
            "pool_size": len(pool),
        },
    }


def _slim_reel(reel: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields needed for download + vision_analyze wiring."""
    return {
        "reel_id": reel.get("reel_id", ""),
        "url": reel.get("url", ""),
        "thumbnail_url": reel.get("thumbnail_url", ""),
        "views": reel.get("views", 0),
        "posted_at": reel.get("posted_at"),
        "posted_within_hours": reel.get("posted_within_hours"),
    }
