"""Background watcher that surfaces gateway ``approval.request`` events
across every API-triggered agent run tracked by ``product_campaign_runs``.

The agent gateway emits ``approval.request`` into the per-run SSE stream
at ``GET /v1/runs/{rid}/events`` whenever a dangerous tool call is
intercepted by ``tools.approval``. The console's transcript panel
already prints these as info rows on the campaign view, but the
operator may be on any page and there's no global "what's waiting"
listing — runs sit blocked until someone navigates to the right
campaign.

This watcher fixes that gap. It:

1. Polls ``product_campaign_runs`` for runs without ``ended_at`` started
   within the last 24h and, for each, opens a long-lived SSE
   subscription to the gateway's per-run event stream.
2. Filters those streams for ``approval.request`` /
   ``approval.responded`` plus the terminal ``run.*`` frames, maintains
   an in-memory ``_pending`` map keyed by ``run_id``, and broadcasts
   request/responded/cleared events to subscribers (the websocket hub
   in ``routers/events.py``).
3. Exposes ``snapshot()`` so a freshly-connected browser can render
   pending approvals immediately, and ``subscribe()`` for incremental
   updates.

Decision rationale (see plan ``fuzzy-wobbling-treasure.md``):

- We subscribe server-side rather than from the browser so a blocked
  agent run still sees a decision while the operator's tab is closed.
- We piggyback on the existing per-run SSE rather than waiting on a
  new global "approvals" channel in the gateway, since the kol-ops
  console already has the registry of run_ids it cares about.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import sqlite3
import threading
from contextlib import suppress
from typing import Any, AsyncIterator, Optional

import httpx

from .config import Settings, get_settings
from .db import _connect
from .perf_snapshot import perf
from .run_status_cache import run_status_cache

log = logging.getLogger(__name__)

# Time window for the scan loop. Older runs are abandoned even if their
# row still has ended_at IS NULL — the gateway evicts terminal-run event
# buffers after ~1h, so a 24h-old run with no ended_at is effectively
# unreachable.
_SCAN_WINDOW_HOURS = 24
_SCAN_INTERVAL_SECONDS = 5.0
# SSE backoff per-run after a 5xx / transport error. Most reconnect
# attempts hit the gateway with the same run_id, so we cap at 30s to
# keep the cost bounded under prolonged gateway outages.
_RECONNECT_BASE_S = 1.0
_RECONNECT_MAX_S = 30.0


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _cutoff_iso(hours: int) -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
    ).isoformat(timespec="seconds")


class GatewayApprovalWatcher:
    """Single-process watcher; tested by injecting a fake SSE source."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._subs: dict[str, asyncio.Task] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._state_lock = threading.Lock()
        self._seq: int = 0
        self._scan_task: Optional[asyncio.Task] = None
        self._settings: Optional[Settings] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._stopped = False

    # ------------------------------------------------------------------ life-cycle

    async def start(self, settings: Optional[Settings] = None) -> None:
        if self._scan_task is not None:
            return
        self._stopped = False
        self._settings = settings or get_settings()
        # ``read=None`` is mandatory: SSE long-poll holds the response open
        # indefinitely, and the default 5s read timeout kills the stream.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0),
        )
        self._scan_task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._scan_task is not None:
            self._scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        for task in list(self._subs.values()):
            task.cancel()
        for task in list(self._subs.values()):
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._subs.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ subscription

    def snapshot(self) -> tuple[list[dict[str, Any]], int]:
        """Return current pending entries + monotonically-increasing seq.

        Callers use ``seq`` to ignore older events that arrived between
        their fetch and websocket subscribe.
        """
        with self._state_lock:
            return list(self._pending.values()), self._seq

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ------------------------------------------------------------------ scan loop

    async def _scan_loop(self) -> None:
        try:
            while not self._stopped:
                try:
                    await self._scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("approval watcher scan error: %s", exc)
                await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return

    async def _scan_once(self) -> None:
        assert self._settings is not None
        # Reap completed subscriptions from the previous cycle before
        # spawning replacements. Deferring this to scan start keeps
        # ``_subs[run_id]`` awaitable in the same tick tests (and any
        # caller) drive a single scan.
        for run_id in list(self._subs):
            task = self._subs[run_id]
            if task.done():
                self._subs.pop(run_id, None)

        rows = await asyncio.to_thread(_read_active_runs, self._settings.db_path)
        use_sse = self._use_sse_per_run(len(rows))
        perf.approval_watcher_mode = (
            "sse_per_run" if use_sse else "poll_aggregate"
        )
        if use_sse:
            for row in rows:
                run_id = row["run_id"]
                if run_id in self._subs and not self._subs[run_id].done():
                    continue
                self._subs.pop(run_id, None)
                self._subs[run_id] = asyncio.create_task(
                    self._watch(
                        run_id=run_id,
                        kind=row["kind"],
                        campaign_id=row["campaign_id"],
                    )
                )
        else:
            for run_id, task in list(self._subs.items()):
                if not task.done():
                    task.cancel()
            self._subs.clear()
        perf.approval_watcher_sse_subs = len(self._subs)
        await self._poll_waiting_approvals(rows)

    def _use_sse_per_run(self, open_count: int) -> bool:
        assert self._settings is not None
        mode = str(
            getattr(self._settings, "approval_watch_mode", None) or "auto"
        ).lower()
        if mode == "poll_aggregate":
            return False
        if mode == "sse_per_run":
            return True
        threshold = max(
            1,
            int(getattr(self._settings, "approval_watch_poll_threshold", 5)),
        )
        return open_count <= threshold

    async def _poll_waiting_approvals(self, rows: list[dict[str, Any]]) -> None:
        """Fallback: surface approvals when gateway status says blocked.

        The SSE subscription can miss ``approval.request`` if the console
        backend restarted after the event was recorded, or if the watcher
        task had not yet subscribed. Polling ``GET /v1/runs/{id}`` catches
        ``waiting_for_approval`` and replays the event history once.
        """
        assert self._settings is not None

        from .deps import get_gateway_singleton

        gateway = get_gateway_singleton()

        async def _probe(row: dict[str, Any]) -> None:
            run_id = str(row.get("run_id") or "")
            if not run_id or run_id.startswith("pending:"):
                return
            with self._state_lock:
                already_pending = run_id in self._pending
            if already_pending:
                return
            try:
                info = await run_status_cache.get_run(gateway, run_id)
            except Exception:  # noqa: BLE001
                return
            if info is None:
                return
            state = str(info.get("status") or "").lower()
            if state != "waiting_for_approval":
                return
            payload = await self._fetch_approval_replay(run_id)
            if payload is None:
                # Status says blocked but we could not read details — still
                # surface a placeholder so operators know to open transcript.
                payload = {
                    "command": "",
                    "description": (
                        "Agent 正在等待危险命令审批。"
                        "请打开活动 transcript 查看完整命令，或稍后重试。"
                    ),
                    "pattern_key": "",
                    "pattern_keys": [],
                    "timestamp": _utcnow_iso(),
                }
            self._open(
                run_id=run_id,
                kind=str(row.get("kind") or ""),
                campaign_id=str(row.get("campaign_id") or ""),
                payload=payload,
            )

        await asyncio.gather(*(_probe(row) for row in rows if row.get("run_id")))

    async def _fetch_approval_replay(self, run_id: str) -> Optional[dict[str, Any]]:
        """One-shot SSE read to recover the latest approval.request frame."""
        assert self._settings is not None and self._client is not None
        url = f"{self._settings.gateway_base.rstrip('/')}/v1/runs/{run_id}/events"
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        if self._settings.gateway_key:
            headers["Authorization"] = f"Bearer {self._settings.gateway_key}"
        latest: Optional[dict[str, Any]] = None
        try:
            async with self._client.stream("GET", url, headers=headers, timeout=8.0) as resp:
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
                        elif inner in {"approval.responded", "run.completed", "run.failed", "run.cancelled"}:
                            if latest is not None:
                                return latest
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

    # ------------------------------------------------------------------ per-run SSE

    async def _watch(self, *, run_id: str, kind: str, campaign_id: str) -> None:
        """Maintain an SSE subscription for one run_id until it ends.

        Retries transport errors with exponential backoff (1s → 30s).
        Exits cleanly on terminal ``run.*`` events, gateway 404 ("run
        already evicted"), or 401 ("auth misconfig — no point retrying").
        """
        attempt = 0
        try:
            while not self._stopped:
                try:
                    terminated = await self._run_sse_once(
                        run_id=run_id, kind=kind, campaign_id=campaign_id,
                    )
                    if terminated:
                        return
                    # Stream closed without a terminal event — reconnect
                    # after a short delay so we don't hot-loop.
                    attempt += 1
                except _UnrecoverableUpstream as exc:
                    log.info(
                        "approval watcher: giving up on run %s (%s)", run_id, exc,
                    )
                    self._close(run_id, reason="evicted")
                    return
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    log.debug("approval watcher transport error run %s: %s", run_id, exc)
                    attempt += 1
                delay = min(_RECONNECT_MAX_S, _RECONNECT_BASE_S * (2 ** attempt))
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
        except asyncio.CancelledError:
            return

    async def _run_sse_once(
        self, *, run_id: str, kind: str, campaign_id: str,
    ) -> bool:
        """Open one SSE connection and consume frames. Return True if
        the run ended (terminal event arrived) so the outer loop can
        stop reconnecting.
        """
        assert self._settings is not None and self._client is not None
        url = f"{self._settings.gateway_base.rstrip('/')}/v1/runs/{run_id}/events"
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        if self._settings.gateway_key:
            headers["Authorization"] = f"Bearer {self._settings.gateway_key}"
        async with self._client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 404:
                raise _UnrecoverableUpstream("gateway 404 — run evicted")
            if resp.status_code == 401:
                raise _UnrecoverableUpstream("gateway 401 — auth rejected")
            if resp.status_code >= 400:
                raise _UnrecoverableUpstream(
                    f"gateway {resp.status_code} — non-retryable"
                )
            event_name = "message"
            data_lines: list[str] = []
            async for raw in resp.aiter_lines():
                if self._stopped:
                    return True
                if raw == "":
                    if data_lines:
                        terminated = self._handle_frame(
                            run_id=run_id, kind=kind, campaign_id=campaign_id,
                            event_name=event_name,
                            data_str="\n".join(data_lines),
                        )
                        if terminated:
                            return True
                    event_name = "message"
                    data_lines = []
                    continue
                if raw.startswith(":"):
                    continue
                if raw.startswith("event:"):
                    event_name = raw[6:].strip() or "message"
                elif raw.startswith("data:"):
                    data_lines.append(raw[5:].lstrip())
        return False

    def _handle_frame(
        self,
        *,
        run_id: str,
        kind: str,
        campaign_id: str,
        event_name: str,
        data_str: str,
    ) -> bool:
        """Parse one SSE data frame; return True iff the run is terminal."""
        try:
            payload: dict[str, Any] = json.loads(data_str)
        except json.JSONDecodeError:
            return False
        # Gateway 0.14+ sets the event header, older builds put it inside
        # the payload — fall back so we don't lose frames.
        inner_event = event_name
        if inner_event == "message":
            inner_event = str(payload.get("event") or "message")
        if inner_event == "approval.request":
            self._open(
                run_id=run_id, kind=kind, campaign_id=campaign_id, payload=payload,
            )
            return False
        if inner_event == "approval.responded":
            choice = payload.get("choice")
            self._close(run_id, reason="responded", choice=str(choice) if choice else None)
            return False
        if inner_event == "run.completed":
            self._close(run_id, reason="run_completed")
            return True
        if inner_event == "run.failed":
            self._close(run_id, reason="run_failed")
            return True
        if inner_event == "run.cancelled":
            self._close(run_id, reason="run_cancelled")
            return True
        return False

    # ------------------------------------------------------------------ state mutations

    def _open(
        self,
        *,
        run_id: str,
        kind: str,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> None:
        entry = {
            "run_id": run_id,
            "campaign_id": campaign_id,
            "kind": kind,
            "command": payload.get("command") or "",
            "description": payload.get("description") or "",
            "pattern_key": payload.get("pattern_key") or "",
            "pattern_keys": payload.get("pattern_keys") or [],
            "choices": payload.get("choices") or ["once", "session", "always", "deny"],
            "source": "gateway",
            "captured_at": payload.get("timestamp") or _utcnow_iso(),
        }
        with self._state_lock:
            self._pending[run_id] = entry
            self._seq += 1
            seq = self._seq
        self._broadcast({
            "event": "gateway_approval.request",
            "seq": seq,
            **entry,
        })

    def _close(self, run_id: str, *, reason: str, choice: Optional[str] = None) -> None:
        with self._state_lock:
            if run_id not in self._pending:
                return
            del self._pending[run_id]
            self._seq += 1
            seq = self._seq
        event_name = (
            "gateway_approval.responded" if choice is not None
            else "gateway_approval.cleared"
        )
        ev: dict[str, Any] = {
            "event": event_name,
            "run_id": run_id,
            "reason": reason,
            "seq": seq,
        }
        if choice is not None:
            ev["choice"] = choice
        self._broadcast(ev)

    def _broadcast(self, ev: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Slow subscriber — drop the event for it. snapshot() at
                # reconnect time will restore consistency.
                pass


class _UnrecoverableUpstream(RuntimeError):
    """Raised inside the SSE loop when retrying won't help."""


def _read_active_runs(db_path: Any) -> list[dict[str, Any]]:
    """Synchronous DB read used inside ``asyncio.to_thread``.

    Returns the most recent open runs across both envs. Env scoping is
    intentionally absent: the same gateway services LIVE and TEST and we
    want approvals from either to surface in the dock.
    """
    conn = _connect(db_path)
    try:
        cutoff = _cutoff_iso(_SCAN_WINDOW_HOURS)
        rows = conn.execute(
            """SELECT run_id, campaign_id, kind
                 FROM product_campaign_runs
                WHERE ended_at IS NULL
                  AND started_at >= ?
                  AND run_id IS NOT NULL
                  AND run_id NOT LIKE 'pending:%'
                ORDER BY started_at DESC
                LIMIT 200""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# Module-level singleton — imported by routers/events.py + main lifespan.
watcher = GatewayApprovalWatcher()


__all__ = ["GatewayApprovalWatcher", "watcher"]
