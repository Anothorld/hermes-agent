"""Pre-tool-call hook for kol-discovery-rpa.

Enforces content-screening modes:

- Vision OFF (default, ``KOL_RPA_VISION_EVAL_ENABLED=0``):
  - In ``kol-campaign:*`` discovery sessions: block ``vision_analyze`` /
    ``video_analyze`` (does NOT affect email-discover / brief-loader /
    other skills that need OCR).
  - Always block cover/video download tools (``rpa_download_ig_*``) —
    those exist only for multimodal content screening.
  Screening is caption + comments via
  ``rpa_fetch_reel_comments(include_caption=true)``.
- Vision ON + video OFF: block ``rpa_download_ig_reel`` (cover batch OK).
- Vision ON + video ON: limit ``rpa_download_ig_reel`` to 3 per candidate.

Also resets per-run pacing quota (profile/reel counters) at each new agent
turn boundary. Gateway rediscover/auto-retry reuses the same
``kol-campaign:LIVE:...`` task_id across runs; ``turn_id`` (unique per
``run_conversation``) is the correct per-run epoch.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Hyphenated directory can't use package imports
_PLUGIN_DIR = Path(__file__).resolve().parent
_INTERNAL_DIR = str(_PLUGIN_DIR / "internal")
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

HookResult = Optional[Union[None, Dict[str, str]]]

_RPA_TOOL_PREFIX = "rpa_"
_DOWNLOAD_TOOL = "rpa_download_ig_reel"
_VISION_TOOLS = frozenset({"vision_analyze", "video_analyze"})
_VISION_ASSET_TOOLS = frozenset({
    "rpa_download_ig_content",
    "rpa_download_ig_cover",
    "rpa_download_ig_reel",
})
_MAX_DOWNLOADS_PER_CANDIDATE = 3

# Align with kol-bridge-agent-guard session taxonomy (discovery vs draft/outreach).
_NON_DISCOVERY_CAMPAIGN_PREFIXES = (
    "kol-campaign-outreach:",
    "kol-campaign-draft:",
)

_lock = threading.Lock()

# task_id → turn epoch that currently owns the pacing/download counters.
# When turn_id changes (new gateway discover run), counters are cleared.
_quota_epoch_by_task: dict[str, str] = {}

# Distinct reel URLs downloaded per task (video mode cap).
_download_reels: dict[str, list[str]] = {}

_TEXT_MODE_MESSAGE = (
    "Vision/multimodal content screening is DISABLED for kol-campaign "
    "discovery (KOL_RPA_VISION_EVAL_ENABLED=0 / brief "
    "rpa_vision_eval_enabled=false). "
    "Do NOT call vision_analyze, video_analyze, rpa_download_ig_content, "
    "rpa_download_ig_cover, or rpa_download_ig_reel for content screening. "
    "Use rpa_fetch_ig_reels → rpa_fetch_reel_comments(mode=evaluation, "
    "include_caption=true) ×10 on cover_reels[].url, then score Showcase/"
    "Match from the author's caption/description + scraped comments only."
)


def _session_key(session_id: str = "", task_id: str = "") -> str:
    """Prefer task_id when it carries the stable KOL session key."""
    sid = (session_id or "").strip()
    tid = (task_id or "").strip()
    for candidate in (tid, sid):
        if candidate.startswith("kol-"):
            return candidate
    return sid or tid


def _is_campaign_discovery_session(session_id: str = "", task_id: str = "") -> bool:
    """True for launch/rediscover ``kol-campaign:ENV:id``, not draft/outreach."""
    key = _session_key(session_id, task_id)
    if not key.startswith("kol-campaign"):
        return False
    return not any(key.startswith(p) for p in _NON_DISCOVERY_CAMPAIGN_PREFIXES)


def _resolve_brief_fields(kwargs: dict) -> dict | None:
    """Try to extract brief fields from the hook kwargs.

    The gateway pre_tool_call hook does not currently inject brief context.
    This is a forward-compatible check — if a future gateway version passes
    brief fields, they will be respected. Otherwise, env is the primary switch.
    """
    for key in ("brief", "brief_fields", "run_brief", "session_brief"):
        val = kwargs.get(key)
        if isinstance(val, dict):
            return val
    return None


def _get_downloaded_reels(task_id: str) -> list[str]:
    with _lock:
        return list(_download_reels.get(task_id, []))


def _add_downloaded_reel(task_id: str, reel_url: str) -> int:
    """Record a reel download and return the new count."""
    with _lock:
        reels = _download_reels.setdefault(task_id, [])
        if reel_url not in reels:
            reels.append(reel_url)
        return len(reels)


def reset_download_count(task_id: str) -> None:
    """Reset download counter for a task (called on run end)."""
    with _lock:
        _download_reels.pop(task_id, None)


def _epoch_for_task(task_id: str, turn_id: str = "") -> str:
    """Return the quota epoch key for ``task_id``.

    Prefer ``turn_id`` (unique per agent turn / gateway run). When absent,
    fall back to a stable legacy key so older callers keep one-shot reset
    semantics instead of clearing on every tool call.
    """
    tid = (task_id or "default").strip() or "default"
    turn = (turn_id or "").strip()
    if turn:
        return turn
    return f"legacy:{tid}"


def maybe_reset_run_quota(task_id: str, turn_id: str = "") -> bool:
    """Clear pacing + download counters when ``task_id`` enters a new turn.

    Returns:
        True if counters were reset for this call.
    """
    tid = (task_id or "default").strip() or "default"
    epoch = _epoch_for_task(tid, turn_id)
    with _lock:
        if _quota_epoch_by_task.get(tid) == epoch:
            return False
        _quota_epoch_by_task[tid] = epoch
        try:
            import pacing as _pacing
            _pacing.reset(tid)
        except Exception:
            pass  # Best-effort — don't block the tool call
        _download_reels.pop(tid, None)
        return True


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    turn_id: str = "",
    **kwargs: Any,
) -> HookResult:
    """Pre-tool-call hook — vision/text mode, video switch, quota reset."""
    del tool_call_id

    from eval_mode import is_vision_eval_enabled, resolve_eval_mode

    brief = _resolve_brief_fields(kwargs)
    vision_on = is_vision_eval_enabled(brief)
    discovery = _is_campaign_discovery_session(session_id, task_id)

    if not vision_on:
        # Download tools are discovery-only multimodal assets — always block.
        if tool_name in _VISION_ASSET_TOOLS:
            return {"action": "block", "message": _TEXT_MODE_MESSAGE}
        # Core vision tools: only block inside campaign discovery so
        # kol-email-discover / creator-brief / other skills keep OCR.
        if tool_name in _VISION_TOOLS and discovery:
            return {"action": "block", "message": _TEXT_MODE_MESSAGE}

    if not tool_name.startswith(_RPA_TOOL_PREFIX):
        return None

    tid = task_id or "default"
    # turn_id may also arrive via kwargs on older plugin loaders.
    effective_turn_id = turn_id or str(kwargs.get("turn_id") or "")
    maybe_reset_run_quota(tid, effective_turn_id)

    # Only enforce download limits on the single-reel video tool
    if tool_name != _DOWNLOAD_TOOL:
        return None

    mode = resolve_eval_mode(brief)

    if mode == "cover":
        return {
            "action": "block",
            "message": (
                "rpa_download_ig_reel is blocked because video eval is OFF "
                "(KOL_RPA_VIDEO_EVAL_ENABLED=0 or brief rpa_video_eval_enabled=false). "
                "Use cover mode: rpa_fetch_ig_reels → rpa_download_ig_content "
                "(covers only) or rpa_download_ig_cover(thumbnail_url) + "
                "rpa_fetch_reel_comments + vision_analyze(cover_path) for content screening."
            ),
        }

    # Video mode — enforce per-candidate download limit (3 distinct reels)
    reel_url = str(args.get("reel_url", "") or "")
    if not reel_url:
        return None  # Let the handler handle missing reel_url

    downloaded = _get_downloaded_reels(tid)
    if reel_url not in downloaded:
        # New reel — check if we've hit the limit
        if len(downloaded) >= _MAX_DOWNLOADS_PER_CANDIDATE:
            return {
                "action": "block",
                "message": (
                    f"rpa_download_ig_reel limit reached: {len(downloaded)} distinct "
                    f"reels already downloaded for this run (max "
                    f"{_MAX_DOWNLOADS_PER_CANDIDATE}). "
                    "Use rpa_download_ig_content for the planned video_reels, or "
                    "rpa_cleanup_reels then continue with another candidate."
                ),
            }
        _add_downloaded_reel(tid, reel_url)

    return None
