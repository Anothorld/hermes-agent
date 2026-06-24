"""Thin httpx wrapper around the Hermes Gateway API server (``/v1/runs/...``).

Used by the console aggregator to surface real-time run lifecycle state
(running / completed / failed / cancelled / waiting_for_approval) without
forcing the front-end to know about port 8642 or auth headers.

We intentionally keep this small: only the read endpoint we need is wired.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

import httpx

from .config import get_settings
from .perf_snapshot import perf

# Mirrors ``APIServerAdapter._MAX_CONCURRENT_RUNS`` in gateway api_server.
GATEWAY_MAX_CONCURRENT_RUNS = 10


class GatewayError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"gateway {status}: {detail}")
        self.status = status
        self.detail = detail


_CONCURRENT_RUNS_RE = re.compile(
    r"too many concurrent runs",
    re.IGNORECASE,
)


def is_gateway_concurrency_limit(exc: GatewayError) -> bool:
    """True when the gateway refused ``POST /v1/runs`` due to run slots."""
    if exc.status == 429 and _CONCURRENT_RUNS_RE.search(exc.detail):
        return True
    if _CONCURRENT_RUNS_RE.search(exc.detail):
        return True
    try:
        payload = json.loads(exc.detail)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if not isinstance(err, dict):
        return False
    message = str(err.get("message") or "")
    code = str(err.get("code") or "")
    return (
        _CONCURRENT_RUNS_RE.search(message) is not None
        or (
            code == "rate_limit_exceeded"
            and _CONCURRENT_RUNS_RE.search(message) is not None
        )
    )


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
        self._drain_tasks: dict[str, asyncio.Task] = {}

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

    async def start_run(
        self,
        *,
        input: str,
        instructions: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        yolo: Optional[bool] = None,
    ) -> dict[str, Any]:
        """POST ``/v1/runs`` — start an async agent run, return ``{run_id,...}``.

        Used by the console's "Start campaign" path to dispatch the
        outreach orchestrator flow against a pre-seeded campaign brief.
        The gateway returns immediately with a ``run_id`` (HTTP 202);
        lifecycle status is later polled via :meth:`get_run`.
        """
        # Long timeout — the gateway processes the run asynchronously
        # but the initial 202 response can sit behind cold-start work
        # (auth, model warm-up, skill discovery).
        body: dict[str, Any] = {"input": input}
        if instructions:
            body["instructions"] = instructions
        if session_id:
            body["session_id"] = session_id
        if model:
            body["model"] = model
        effective_yolo = yolo if yolo is not None else get_settings().gateway_yolo
        if effective_yolo:
            body["yolo"] = True
        url = f"{self._base}/v1/runs"
        try:
            r = await self._client.post(
                url, headers=self._headers, json=body, timeout=30.0
            )
        except httpx.HTTPError as exc:
            raise GatewayError(502, f"gateway unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text)
        return r.json()

    async def start_run_with_retry(
        self,
        *,
        retries: int = 2,
        retry_delay_sec: float = 1.5,
        concurrency_retries: int = 8,
        concurrency_retry_delay_sec: float = 5.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Like :meth:`start_run`, retrying transient and concurrency errors.

        Transport blips (502/503/504) use short linear backoff. Gateway
        ``429 Too many concurrent runs`` uses longer waits so an in-flight
        draft/outreach run can finish and free a slot before we surface
        an operator-facing error.
        """
        last_exc: GatewayError | None = None
        transport_attempt = 0
        concurrency_attempt = 0
        while True:
            try:
                return await self.start_run(**kwargs)
            except GatewayError as exc:
                last_exc = exc
                if (
                    is_gateway_concurrency_limit(exc)
                    and concurrency_attempt < concurrency_retries
                ):
                    concurrency_attempt += 1
                    await asyncio.sleep(
                        min(
                            concurrency_retry_delay_sec * concurrency_attempt,
                            15.0,
                        )
                    )
                    continue
                if (
                    exc.status in (502, 503, 504)
                    and transport_attempt < retries
                ):
                    transport_attempt += 1
                    await asyncio.sleep(retry_delay_sec * transport_attempt)
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise GatewayError(502, "gateway start_run failed without detail")

    async def resolve_approval(
        self, run_id: str, *, choice: str,
    ) -> dict[str, Any]:
        """POST ``/v1/runs/{id}/approval`` — resolve a pending approval.

        ``choice`` is one of ``once / session / always / deny``. The
        gateway returns ``{run_id, choice, resolved}`` on success, 404
        when the run is unknown, 409 when no approval is currently
        pending.
        """
        url = f"{self._base}/v1/runs/{run_id}/approval"
        try:
            r = await self._client.post(
                url, headers=self._headers, json={"choice": choice},
            )
        except httpx.HTTPError as exc:
            raise GatewayError(502, f"gateway unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text)
        return r.json()

    async def find_latest_approval_request(
        self,
        run_id: str,
        *,
        timeout_sec: float = 8.0,
    ) -> Optional[dict[str, Any]]:
        """Return the latest ``approval.request`` event from a run's SSE replay.

        Used by :mod:`gateway_approval_watcher` as a fallback when the
        long-lived SSE subscription missed the frame (late watcher start,
        reconnect race, or console restart while a run is already blocked).
        """
        url = f"{self._base}/v1/runs/{run_id}/events"
        headers = {**self._headers, "Accept": "text/event-stream"}
        latest: Optional[dict[str, Any]] = None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=timeout_sec, write=5.0, pool=5.0),
            ) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code >= 400:
                        return None
                    event_name = "message"
                    data_lines: list[str] = []
                    async for raw in resp.aiter_lines():
                        if raw == "":
                            if not data_lines:
                                event_name = "message"
                                continue
                            try:
                                payload = json.loads("\n".join(data_lines))
                            except json.JSONDecodeError:
                                data_lines = []
                                event_name = "message"
                                continue
                            inner = event_name
                            if inner == "message":
                                inner = str(payload.get("event") or "message")
                            if inner == "approval.request":
                                latest = payload
                            data_lines = []
                            event_name = "message"
                            continue
                        if raw.startswith(":"):
                            continue
                        if raw.startswith("event:"):
                            event_name = raw[6:].strip() or "message"
                        elif raw.startswith("data:"):
                            data_lines.append(raw[5:].lstrip())
        except httpx.HTTPError:
            return latest
        return latest

    async def drain_run_events(self, run_id: str) -> None:
        """Consume ``GET /v1/runs/{id}/events`` until the stream closes.

        Gateway counts each started run against ``_MAX_CONCURRENT_RUNS``
        until something reads the SSE feed (or the 300s orphan sweep).
        Fire-and-forget callers (email discovery, recovery scripts) must
        drain so slots free promptly when the agent finishes.
        """
        url = f"{self._base}/v1/runs/{run_id}/events"
        headers = {**self._headers, "Accept": "text/event-stream"}
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code >= 400:
                        return
                    async for _ in resp.aiter_bytes():
                        pass
        except httpx.HTTPError:
            return

    def schedule_drain_run_events(self, run_id: str) -> None:
        """Background drain — idempotent per ``run_id``."""
        self.ensure_run_drained(run_id)

    def ensure_run_drained(self, run_id: str) -> None:
        """Start at most one background SSE drain per run."""
        if not run_id or run_id.startswith("pending:"):
            return
        existing = self._drain_tasks.get(run_id)
        if existing is not None and not existing.done():
            return

        async def _runner() -> None:
            try:
                await self.drain_run_events(run_id)
            finally:
                self._drain_tasks.pop(run_id, None)
                perf.gateway_drain_tasks = len(self._drain_tasks)

        task = asyncio.create_task(_runner())
        self._drain_tasks[run_id] = task
        perf.gateway_drain_tasks = len(self._drain_tasks)

    async def launch_via_queue(
        self,
        start_fn,
        *,
        session_id: str,
        dedup_key: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a run through :mod:`run_launch_queue` when enabled."""
        from .run_launch_queue import _BROWSER_SERIAL_KINDS, launch_queue

        result = await launch_queue.launch(
            start_fn,
            session_id=session_id,
            dedup_key=dedup_key,
            kind=kind,
        )
        run = result.run
        run_id = run.get("run_id") if isinstance(run, dict) else None
        resolved_kind = kind or launch_queue._kind_from_session(session_id)
        if (
            isinstance(run_id, str)
            and run_id
            and resolved_kind not in _BROWSER_SERIAL_KINDS
        ):
            self.ensure_run_drained(run_id)
        if result.queued:
            run = dict(run)
            run["_queued"] = True
            run["_waited_sec"] = result.waited_sec
            run["_queue_position"] = result.queue_position
        return run

    async def stop_run(self, run_id: str) -> dict[str, Any]:
        """POST ``/v1/runs/{id}/stop`` — interrupt a running agent.

        Returns the gateway's stop ack (typically ``{"status": "stopping"}``).
        ``404`` is mapped to a no-op return so the console can call this
        idempotently on already-evicted runs.
        """
        url = f"{self._base}/v1/runs/{run_id}/stop"
        try:
            r = await self._client.post(url, headers=self._headers)
        except httpx.HTTPError as exc:
            raise GatewayError(502, f"gateway unreachable: {exc}") from exc
        if r.status_code == 404:
            return {"status": "not_found", "run_id": run_id}
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text)
        return r.json()
