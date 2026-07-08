"""Evaluation mode switch — cover (default) vs video (ON).

Priority: brief field > env variable > default (OFF).

When OFF (default):
    10 covers + 10 comments → vision_analyze(cover) + comments
When ON:
    10 covers + 3 videos + 10 comments → above + video_analyze(top 3)

The ``hooks.py`` pre_tool_call hook blocks ``rpa_download_ig_reel`` when
OFF, and limits to 3 downloads per candidate when ON.

Brief field: ``rpa_video_eval_enabled: true|false``
Env variable: ``KOL_RPA_VIDEO_EVAL_ENABLED=1|0``
"""

from __future__ import annotations

import os

EVAL_COVER_COUNT = 10
EVAL_VIDEO_COUNT = 3


def resolve_eval_mode(brief_fields: dict | None = None) -> str:
    """Resolve the video-eval switch to ``"video"`` or ``"cover"``.

    Args:
        brief_fields: The gateway brief dict (may contain
            ``rpa_video_eval_enabled``).

    Returns:
        ``"video"`` if switch is ON, ``"cover"`` if OFF (default).
    """
    # Brief field takes priority
    if brief_fields is not None:
        brief_val = brief_fields.get("rpa_video_eval_enabled")
        if brief_val is True:
            return "video"
        if brief_val is False:
            return "cover"

    # Env variable fallback
    env_val = os.environ.get("KOL_RPA_VIDEO_EVAL_ENABLED", "").strip().lower()
    if env_val in ("1", "true", "yes", "on"):
        return "video"
    if env_val in ("0", "false", "no", "off"):
        return "cover"

    # Default: OFF (cover mode — saves tokens, aligns with skill L611-615)
    return "cover"


def is_video_mode(brief_fields: dict | None = None) -> bool:
    """Convenience: return True if video mode is ON."""
    return resolve_eval_mode(brief_fields) == "video"
