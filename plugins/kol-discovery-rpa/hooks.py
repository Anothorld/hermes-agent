"""Pre-tool-call hook for kol-discovery-rpa.

Enforces the video-eval switch:
- When OFF (default): block ``rpa_download_ig_reel`` — cover mode only.
- When ON: limit ``rpa_download_ig_reel`` to 3 distinct reels per run epoch.

Also resets per-run pacing quota (profile/reel counters) at each new agent
turn boundary. Gateway rediscover/auto-retry reuses the same
``kol-campaign:LIVE:...`` task_id across runs; ``turn_id`` (unique per
``run_conversation``) is the correct per-run epoch. Without turn-scoped
reset, exhausted counters (e.g. 94/40) stick across launches.
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
_MAX_DOWNLOADS_PER_CANDIDATE = 3

_lock = threading.Lock()

# task_id → turn epoch that currently owns the pacing/download counters.
# When turn_id changes (new gateway discover run), counters are cleared.
_quota_epoch_by_task: dict[str, str] = {}

# Distinct reel URLs downloaded per task (video mode cap).
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
    """Pre-tool-call hook — video switch, download limits, turn-scoped quota reset."""
    del tool_call_id, session_id

    if not tool_name.startswith(_RPA_TOOL_PREFIX):
        return None

    tid = task_id or "default"
    # turn_id may also arrive via kwargs on older plugin loaders.
    effective_turn_id = turn_id or str(kwargs.get("turn_id") or "")
    maybe_reset_run_quota(tid, effective_turn_id)

    # Only enforce download limits on the single-reel video tool
    if tool_name != _DOWNLOAD_TOOL:
        return None

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
        if len(downloaded) >= _MAX_DOWNLOADS_PER_CANDIDATE:
            return {
                "action": "block",
                "message": (
                    f"rpa_download_ig_reel limit reached: {len(downloaded)} distinct "
                    f"reels already downloaded for this run (max "
                    f"{_MAX_DOWNLOADS_PER_CANDIDATE}). "
                    "Proceed with the downloaded videos + 2 covers + comments."
                ),
            }
        _add_downloaded_reel(tid, reel_url)

    return None
