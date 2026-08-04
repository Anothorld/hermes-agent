"""Evaluation mode — text (default) / cover / video.

Priority for vision: brief ``rpa_vision_eval_enabled`` > env
``KOL_RPA_VISION_EVAL_ENABLED`` > default OFF (text-only).

When vision is OFF (default, 2026-08 temporary ops policy):
    10× ``rpa_fetch_reel_comments(include_caption=true)`` only —
    caption (author description) + comments. No cover download,
    ``vision_analyze``, or ``video_analyze``.

When vision is ON:
    Video switch (brief ``rpa_video_eval_enabled`` / env
    ``KOL_RPA_VIDEO_EVAL_ENABLED``) selects cover vs cover+video:
    - OFF → 10 covers + 10 comments → ``vision_analyze`` + comments
    - ON  → above + 3 random videos → ``video_analyze``

``hooks.py`` blocks multimodal tools when vision is OFF, blocks
``rpa_download_ig_reel`` when cover-only, and limits downloads when video ON.
"""

from __future__ import annotations

import os

EVAL_COVER_COUNT = 10
EVAL_VIDEO_COUNT = 3

_FALSEY = ("0", "false", "no", "off", "")
_TRUTHY = ("1", "true", "yes", "on")


def _env_flag(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower()


def is_vision_eval_enabled(brief_fields: dict | None = None) -> bool:
    """Return True when cover/video multimodal screening is allowed.

    Default is False — content screening uses caption + comments only.
    """
    if brief_fields is not None and "rpa_vision_eval_enabled" in brief_fields:
        return bool(brief_fields.get("rpa_vision_eval_enabled"))

    env_val = _env_flag("KOL_RPA_VISION_EVAL_ENABLED")
    if env_val is None:
        return False
    if env_val in _TRUTHY:
        return True
    if env_val in _FALSEY:
        return False
    return False


def resolve_eval_mode(brief_fields: dict | None = None) -> str:
    """Resolve screening mode to ``"text"``, ``"cover"``, or ``"video"``.

    Args:
        brief_fields: Optional gateway brief dict.

    Returns:
        ``"text"`` when vision is disabled (default);
        ``"video"`` when vision ON and video switch ON;
        ``"cover"`` when vision ON and video switch OFF.
    """
    if not is_vision_eval_enabled(brief_fields):
        return "text"

    # Brief field takes priority for video switch
    if brief_fields is not None:
        brief_val = brief_fields.get("rpa_video_eval_enabled")
        if brief_val is True:
            return "video"
        if brief_val is False:
            return "cover"

    env_val = _env_flag("KOL_RPA_VIDEO_EVAL_ENABLED")
    if env_val in _TRUTHY:
        return "video"
    if env_val in _FALSEY:
        return "cover"

    # Vision ON but video unspecified → cover mode
    return "cover"


def is_video_mode(brief_fields: dict | None = None) -> bool:
    """Convenience: return True if video mode is ON."""
    return resolve_eval_mode(brief_fields) == "video"


def is_text_mode(brief_fields: dict | None = None) -> bool:
    """Convenience: return True if multimodal vision is disabled."""
    return resolve_eval_mode(brief_fields) == "text"
