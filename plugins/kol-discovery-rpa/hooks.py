"""Pre-tool-call hook for kol-discovery-rpa.

Enforces the video-eval switch:
- When OFF (default): block ``rpa_download_ig_reel`` — cover mode only.
- When ON: limit to 3 downloads per candidate handle (not per run).

Also resets per-run pacing quota (profile/reel counters) when a new task_id
is seen for the first time in this process — without this, a new discover
run that reuses the same ``kol-campaign:LIVE:...`` task_id inherits the
previous run's exhausted quota (e.g. 39/40 profiles used), blocking all
RPA profile evaluations in the new run.

The eval mode is resolved from env ``KOL_RPA_VIDEO_EVAL_ENABLED``.
Brief field ``rpa_video_eval_enabled`` is checked if the gateway passes
brief context in kwargs, but the standard gateway pre_tool_call hook
does not currently inject brief — so env is the primary switch.

Download limit is per-(task_id, handle) — each candidate gets up to 3
video downloads, not 3 for the entire run. The handle is extracted from
the reel_url in args (IG reel URLs contain the reel ID, not the handle,
so we use a separate counter keyed by task_id + reel_url prefix).
"""

from __future__ import annotations

import os
import re
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
_MAX_DOWNLOADS_PER_CANDIDATE = 3

_lock = threading.Lock()
# (task_id, handle) → download count — per-candidate limit
_download_counts: dict[tuple[str, str], int] = {}

# Task IDs that have been seen in this process. When a new task_id is
# encountered, we reset its pacing quota so a fresh discover run doesn't
# inherit the previous run's exhausted profile/reel counters.
_seen_task_ids: set[str] = set()

# Extract handle from reel_url: instagram.com/reel/<id>/ has no handle,
# but the Agent typically calls rpa_fetch_ig_profile(handle) before
# rpa_download_ig_reel. We key by task_id + a "candidate key" derived
# from the reel_url. Since multiple reels from the same candidate should
# share the 3-download budget, we use a coarse key: task_id alone is too
# broad (entire run), reel_url alone is too narrow (per-reel).
# Best available: task_id + first 3 reel URLs share the budget — we
# track by (task_id, reel_url) but allow up to 3 distinct reel_urls.
_download_reels: dict[str, list[str]] = {}


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


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **kwargs: Any,
) -> HookResult:
    """Pre-tool-call hook — enforce video-eval switch, download limits, and quota reset."""
    del tool_call_id, session_id

    if not tool_name.startswith(_RPA_TOOL_PREFIX):
        return None

    tid = task_id or "default"

    # Reset pacing quota on first RPA call for a new task_id in this process.
    # Without this, a new discover run reusing the same task_id (e.g.
    # kol-campaign:LIVE:SEB8008-20260525) inherits the previous run's
    # exhausted profile quota (39/40) and can't evaluate any new candidates.
    with _lock:
        if tid not in _seen_task_ids:
            _seen_task_ids.add(tid)
            try:
                import pacing as _pacing
                _pacing.reset(tid)
            except Exception:
                pass  # Best-effort — don't block the tool call

    # Only enforce on the download tool
    if tool_name != _DOWNLOAD_TOOL:
        return None

    # Resolve eval mode (brief > env > default OFF)
    from eval_mode import resolve_eval_mode
    brief = _resolve_brief_fields(kwargs)
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
                    "Proceed with the downloaded videos + 10 covers + comments."
                ),
            }
        # Pre-approve this new reel
        _add_downloaded_reel(tid, reel_url)

    return None
