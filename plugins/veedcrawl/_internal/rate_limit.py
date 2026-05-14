"""Rate-limit helpers for Veedcrawl responses.

Veedcrawl returns three headers on every response:

- ``X-RateLimit-Limit``     — total requests allowed in current window
- ``X-RateLimit-Remaining`` — requests left before the limit resets
- ``X-RateLimit-Reset``     — Unix timestamp when the window resets

On a 429 we sleep until ``reset`` (plus a small jitter to avoid thundering
herd) and retry exactly once. A second 429 surfaces as a structured error.
"""

from __future__ import annotations

import random
import time
from typing import Mapping, Optional


def parse_remaining(headers: Mapping[str, str]) -> Optional[int]:
    raw = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_reset(headers: Mapping[str, str]) -> Optional[float]:
    raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def sleep_until_reset(
    headers: Mapping[str, str],
    *,
    now: float,
    sleep: callable,
    max_wait_s: float = 60.0,
    jitter_s: float = 0.5,
) -> float:
    """Block until the rate-limit window resets. Returns the actual wait time.

    Caps the wait at ``max_wait_s`` so a malformed/distant ``Reset`` value
    cannot freeze the agent indefinitely.
    """
    reset = parse_reset(headers)
    if reset is None:
        wait = 1.0
    else:
        wait = max(0.0, reset - now) + random.uniform(0.0, jitter_s)
    wait = min(wait, max_wait_s)
    if wait > 0:
        sleep(wait)
    return wait


def real_sleep(seconds: float) -> None:
    """Default blocking sleep (separate symbol so tests can monkeypatch)."""
    time.sleep(seconds)
