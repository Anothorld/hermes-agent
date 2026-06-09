"""Short-TTL cache for gateway ``GET /v1/runs/{id}`` to dedupe polling."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .config import get_settings
from .gateway_client import GatewayClient, GatewayError
from .perf_snapshot import perf

_APPROVAL_STATES = frozenset({"waiting_for_approval"})


class RunStatusCache:
    """Process-wide cache shared by reconciler, watcher, and dock."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    def _ttl_for(self, info: Optional[dict[str, Any]]) -> float:
        if info is None:
            return get_settings().run_status_cache_ttl_sec
        state = str(info.get("status") or "").lower()
        if state in _APPROVAL_STATES or state in {"queued", "running", "stopping"}:
            return get_settings().run_status_cache_active_ttl_sec
        return get_settings().run_status_cache_ttl_sec

    async def get_run(
        self,
        gateway: GatewayClient,
        run_id: str,
        *,
        bypass_cache: bool = False,
    ) -> Optional[dict[str, Any]]:
        if not run_id:
            return None
        now = time.monotonic()
        if not bypass_cache:
            async with self._lock:
                hit = self._data.get(run_id)
            if hit is not None:
                expires_at, payload = hit
                if now < expires_at:
                    perf.gateway_get_run_cache_hits += 1
                    return payload

        perf.gateway_get_run_cache_misses += 1
        try:
            info = await gateway.get_run(run_id)
        except GatewayError:
            raise
        ttl = self._ttl_for(info)
        async with self._lock:
            self._data[run_id] = (now + ttl, info)
        return info

    def invalidate(self, run_id: str) -> None:
        self._data.pop(run_id, None)

    def clear(self) -> None:
        self._data.clear()


run_status_cache = RunStatusCache()
