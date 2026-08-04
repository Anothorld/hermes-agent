"""Pacing + per-run quota enforcement for RPA browser operations.

Enforces the conservative browsing rules from ``instagram-kol-discovery``
skill: random 2-4s between profiles, 1-2s between reels,
max 80 profiles and 400 reel page loads per run
(override via ``KOL_RPA_MAX_*_PER_RUN``).

Quota counters are per-task (same key as tab-pool ``task_id``) and
in-process (single gateway worker assumption, same as
``discovery_session.py``). ``rpa_precheck_handle`` and ``rpa_check_ip``
do NOT count against profile quota — they perform zero page loads.
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Hyphenated directory can't use package imports — add to sys.path
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

# Defaults — overridable via env for tuning
_PROFILE_DELAY = tuple(
    float(x) for x in os.environ.get(
        "KOL_RPA_PROFILE_DELAY_S", "2.0,4.0"
    ).split(",")
)
_REEL_DELAY = tuple(
    float(x) for x in os.environ.get(
        "KOL_RPA_REEL_DELAY_S", "1.0,2.0"
    ).split(",")
)
_MAX_PROFILES = int(os.environ.get("KOL_RPA_MAX_PROFILES_PER_RUN", "80"))
_MAX_REEL_LOADS = int(os.environ.get("KOL_RPA_MAX_REEL_LOADS_PER_RUN", "400"))
_RATE_LIMIT_BACKOFF = tuple(
    float(x) for x in os.environ.get(
        "KOL_RPA_RATE_LIMIT_BACKOFF_S", "30,120"
    ).split(",")
)

_lock = threading.Lock()
_counters: dict[str, _RunQuota] = {}


@dataclass
class _RunQuota:
    profiles_used: int = 0
    reel_loads_used: int = 0
    surface_blocked: dict[str, bool] = field(default_factory=dict)


def _get(task_id: str) -> _RunQuota:
    with _lock:
        q = _counters.get(task_id)
        if q is None:
            q = _RunQuota()
            _counters[task_id] = q
        return q


def reset(task_id: str) -> None:
    """Clear quota for a task.

    Called by ``hooks.maybe_reset_run_quota`` when a new agent turn/gateway
    discover run starts for ``task_id`` (and on explicit cleanup).
    """
    with _lock:
        _counters.pop(task_id, None)


def jitter_delay(kind: str = "profile") -> None:
    """Sleep a random jitter before navigating to mimic human pacing.

    Args:
        kind: ``"profile"`` (2-4s) or ``"reel"`` (1-2s).
    """
    lo, hi = _PROFILE_DELAY if kind == "profile" else _REEL_DELAY
    time.sleep(random.uniform(lo, hi))


def rate_limit_backoff() -> None:
    """Sleep a longer backoff after a rate-limit signal (30-120s)."""
    lo, hi = _RATE_LIMIT_BACKOFF
    time.sleep(random.uniform(lo, hi))


def mark_profile(task_id: str) -> None:
    """Increment profile visit counter. Raises QuotaExceededError if over cap."""
    import errors as _errors
    q = _get(task_id)
    with _lock:
        q.profiles_used += 1
        if q.profiles_used > _MAX_PROFILES:
            raise _errors.QuotaExceededError(
                f"profile visits ({q.profiles_used}) exceed per-run cap ({_MAX_PROFILES})"
            )


def mark_reel_load(task_id: str) -> None:
    """Increment reel page load counter. Raises QuotaExceededError if over cap."""
    import errors as _errors
    q = _get(task_id)
    with _lock:
        q.reel_loads_used += 1
        if q.reel_loads_used > _MAX_REEL_LOADS:
            raise _errors.QuotaExceededError(
                f"reel page loads ({q.reel_loads_used}) exceed per-run cap ({_MAX_REEL_LOADS})"
            )


def mark_surface_blocked(task_id: str, surface: str) -> None:
    """Mark a discovery surface as rate-limited for this run."""
    q = _get(task_id)
    with _lock:
        q.surface_blocked[surface] = True


def is_surface_blocked(task_id: str, surface: str) -> bool:
    q = _get(task_id)
    with _lock:
        return q.surface_blocked.get(surface, False)


def quota_snapshot(task_id: str) -> dict:
    """Return current quota usage for inclusion in tool ``meta``."""
    q = _get(task_id)
    with _lock:
        return {
            "profiles_used": q.profiles_used,
            "profiles_cap": _MAX_PROFILES,
            "reel_loads_used": q.reel_loads_used,
            "reel_loads_cap": _MAX_REEL_LOADS,
        }
