"""Per-campaign asyncio locks for serializing discovery-gate workflows.

The discovery gate (auto-retry rediscover after a terminal run) and the
operator-initiated ``/rediscover`` + ``/approve-shortlist`` flows share
state on ``product_campaigns`` and ``product_campaign_runs``. SQLite is in
autocommit mode with no per-request transaction boundary, so the natural
race window between "check in-flight → start gateway run → register run →
UPDATE row" is several hundred ms wide. Two GETs hitting
``/products/{sku}/campaigns`` at the same time can both observe a
running→terminal flip, both call ``evaluate_gate_after_terminal``, both
pass the TTL dedup check, and both spawn an auto-retry.

This module hands out a single ``asyncio.Lock`` per ``(env, campaign_id)``
key, scoped to the process. All write paths that touch the gate state
must acquire the lock for that key before doing the inflight check /
start_run / UPDATE sequence. The lock is in-memory only — running more
than one console process behind a load balancer would require a real
distributed lock (Redis/Postgres advisory). For now the console is a
single-process FastAPI app, so this is sufficient.
"""

from __future__ import annotations

import asyncio
from typing import Tuple


_LOCKS: dict[Tuple[str, str], asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def campaign_lock(env: str, campaign_id: str) -> asyncio.Lock:
    """Return the process-wide ``asyncio.Lock`` for one campaign key.

    The lookup itself is guarded by a meta-lock so two concurrent callers
    cannot each create + return a different Lock instance for the same
    key — that would defeat the whole point.
    """
    key = (env, campaign_id)
    async with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock
