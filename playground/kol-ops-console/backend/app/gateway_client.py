"""Thin httpx wrapper around the Hermes Gateway API server (``/v1/runs/...``).

Used by the console aggregator to surface real-time run lifecycle state
(running / completed / failed / cancelled / waiting_for_approval) without
forcing the front-end to know about port 8642 or auth headers.

We intentionally keep this small: only the read endpoint we need is wired.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .config import get_settings


class GatewayError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"gateway {status}: {detail}")
        self.status = status
        self.detail = detail


# Run lifecycle status values that mean "still doing work".
RUNNING_STATES = frozenset({"queued", "running", "waiting_for_approval", "stopping"})

# Terminal states that should flip the console-tracked ``status`` to closed.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class GatewayClient:
    def __init__(self) -> None:
        s = get_settings()
        self._base = s.gateway_base.rstrip("/")
        self._headers: dict[str, str] = {}
        if s.gateway_key:
            self._headers["Authorization"] = f"Bearer {s.gateway_key}"
        # Short timeout — this is a polling read against localhost.
        self._client = httpx.AsyncClient(timeout=5.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Return the gateway's run-status object, or ``None`` if not found.

        Gateway evicts terminal runs after ~1h (``_RUN_STATUS_TTL``), so a
        ``None`` from a once-known run_id means "gateway no longer remembers
        it; assume it has long since finished".
        """
        url = f"{self._base}/v1/runs/{run_id}"
        try:
            r = await self._client.get(url, headers=self._headers)
        except httpx.HTTPError as exc:
            raise GatewayError(502, f"gateway unreachable: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text)
        return r.json()
