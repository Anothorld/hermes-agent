"""Pre-dispatch argument validation for Veedcrawl tools.

Catches empty or incomplete tool calls before handlers run so the model gets
an actionable block message (with example JSON) instead of a generic
``bad_request`` after a no-op validation pass.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def _nonempty(args: Mapping[str, Any], key: str) -> Optional[str]:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _has_username_or_url(args: Mapping[str, Any]) -> bool:
    return bool(_nonempty(args, "username") or _nonempty(args, "url"))


def validate_tool_args(tool_name: str, args: Mapping[str, Any] | None) -> Optional[str]:
    """Return a block message when ``args`` are incomplete, else ``None``."""
    payload = dict(args or {})

    if tool_name == "veedcrawl_instagram_profile":
        if _has_username_or_url(payload):
            return None
        return (
            "veedcrawl_instagram_profile requires username or url. "
            'Example: {"username": "creator_handle"} or '
            '{"url": "https://www.instagram.com/creator_handle/"}'
        )

    if tool_name == "veedcrawl_profile":
        platform = (_nonempty(payload, "platform") or "").lower()
        if platform not in {"instagram", "tiktok"}:
            return (
                "veedcrawl_profile requires platform (instagram or tiktok) "
                "and username or url. "
                'Example: {"platform": "instagram", "username": "creator_handle"}'
            )
        if not _has_username_or_url(payload):
            return (
                "veedcrawl_profile requires username or url in addition to platform. "
                'Example: {"platform": "instagram", "username": "creator_handle"}'
            )
        return None

    if tool_name == "veedcrawl_search_social_videos":
        if _nonempty(payload, "q"):
            return None
        return (
            "veedcrawl_search_social_videos requires non-empty q. "
            'Example: {"q": "cozy living room makeover", "platform": "instagram", "limit": 6}'
        )

    if tool_name == "veedcrawl_extract":
        if _nonempty(payload, "job_id"):
            return None
        url = _nonempty(payload, "url")
        prompt = _nonempty(payload, "prompt")
        if url and prompt:
            return None
        return (
            "veedcrawl_extract requires url+prompt to start a job, or job_id alone "
            "to fetch an existing job. "
            'Example: {"url": "https://www.instagram.com/reel/abc/", '
            '"prompt": "Summarize the creator niche and product mentions."}'
        )

    if tool_name == "veedcrawl_metadata":
        if _nonempty(payload, "url"):
            return None
        return (
            "veedcrawl_metadata requires url. "
            'Example: {"url": "https://www.instagram.com/reel/abc/"}'
        )

    if tool_name == "veedcrawl_transcript":
        if _nonempty(payload, "job_id"):
            return None
        if _nonempty(payload, "url"):
            return None
        return (
            "veedcrawl_transcript requires url to start a job, or job_id alone "
            "to fetch an existing job. "
            'Example: {"url": "https://www.youtube.com/watch?v=abc", "mode": "auto"}'
        )

    if tool_name == "veedcrawl_job":
        endpoint = (_nonempty(payload, "endpoint") or "").lower()
        job_id = _nonempty(payload, "job_id")
        if endpoint in {"transcript", "extract"} and job_id:
            return None
        return (
            "veedcrawl_job requires endpoint (transcript or extract) and job_id. "
            'Example: {"endpoint": "extract", "job_id": "<id from prior call>"}'
        )

    return None
