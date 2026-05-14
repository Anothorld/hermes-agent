"""Polling helper for Veedcrawl async jobs (``transcript`` / ``extract``).

Veedcrawl's async endpoints return ``{"jobId": ..., "status": "queued"}`` and
expect the client to poll ``GET /v1/<endpoint>/{jobId}`` until ``status`` is
``"completed"`` or ``"failed"``. We use a capped exponential backoff so cheap
jobs return fast (~1 s) while long jobs do not hammer the server.
"""

from __future__ import annotations

from typing import Callable

# Backoff schedule in seconds. Repeats the last entry until ``timeout_s`` is
# exhausted. Total wait through the schedule once is ~29 s, then 10 s/poll.
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0, 8.0, 10.0)


def backoff_sequence(timeout_s: float) -> list[float]:
    """Return the sleep intervals up to (but not exceeding) ``timeout_s`` total."""
    out: list[float] = []
    elapsed = 0.0
    idx = 0
    while elapsed < timeout_s:
        delay = _BACKOFF_SCHEDULE[min(idx, len(_BACKOFF_SCHEDULE) - 1)]
        # Trim final wait so we do not exceed the budget.
        delay = min(delay, max(0.0, timeout_s - elapsed))
        if delay <= 0:
            break
        out.append(delay)
        elapsed += delay
        idx += 1
    return out


def poll(
    fetch: Callable[[], dict],
    *,
    timeout_s: float,
    sleep: Callable[[float], None],
) -> dict:
    """Call ``fetch()`` repeatedly until terminal status, returning the final payload.

    Args:
        fetch: zero-arg callable returning the raw job payload (must include
            ``status`` key).
        timeout_s: hard upper bound on total wall time spent polling.
        sleep: blocking sleep function (injectable for tests).

    Returns:
        The final job payload (caller decides whether ``status`` is acceptable).

    Raises:
        TimeoutError: if no terminal status reached within ``timeout_s``.
    """
    payload = fetch()
    status = str(payload.get("status") or "").lower()
    if status in {"completed", "failed"}:
        return payload
    for delay in backoff_sequence(timeout_s):
        sleep(delay)
        payload = fetch()
        status = str(payload.get("status") or "").lower()
        if status in {"completed", "failed"}:
            return payload
    raise TimeoutError(
        f"Veedcrawl job did not finish within {timeout_s:.1f}s "
        f"(last status={payload.get('status')!r})"
    )
