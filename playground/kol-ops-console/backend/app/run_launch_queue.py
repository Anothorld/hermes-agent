"""Gateway run launch queue — caps concurrency and serializes email discovery."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .config import get_settings
from .gateway_client import GatewayClient, GatewayError, is_gateway_concurrency_limit
from .perf_snapshot import perf

log = logging.getLogger(__name__)

StartFn = Callable[[], Awaitable[dict[str, Any]]]
BridgeHealthFn = Callable[[], Awaitable[bool]]
_bridge_health_fn: Optional[BridgeHealthFn] = None


def set_bridge_health_check(fn: Optional[BridgeHealthFn]) -> None:
    """Register async bridge health probe used before each dequeue start."""
    global _bridge_health_fn
    _bridge_health_fn = fn

_CONCURRENCY_RETRIES = 8
_CONCURRENCY_RETRY_BASE_SEC = 5.0
_BRIDGE_HEALTH_RETRIES = 3
_BRIDGE_HEALTH_RETRY_SEC = 1.5


@dataclass
class LaunchResult:
    """Outcome of a queued or immediate gateway start."""

    run: dict[str, Any]
    queued: bool = False
    queue_position: int = 0
    waited_sec: float = 0.0


@dataclass(order=True)
class _QueueItem:
    priority: int
    seq: int
    kind: str
    session_id: str
    dedup_key: Optional[str]
    start_fn: StartFn = field(compare=False)
    future: asyncio.Future[LaunchResult] = field(compare=False)


class RunLaunchQueue:
    """Single-process launch scheduler with per-kind concurrency caps."""

    def __init__(self) -> None:
        self._seq = 0
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._pending_items: list[_QueueItem] = []
        self._worker_task: Optional[asyncio.Task] = None
        self._general_sem: Optional[asyncio.Semaphore] = None
        self._email_sem = asyncio.Semaphore(1)
        self._recovery_sem = asyncio.Semaphore(1)
        self._inflight_kinds: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _kind_from_session(self, session_id: str) -> str:
        if session_id.startswith("kol-email-discover:"):
            return "email_discover"
        if ":recovery-" in session_id:
            return "recovery"
        return "general"

    def _priority_for(self, kind: str) -> int:
        if kind == "recovery":
            return 2
        if kind == "email_discover":
            return 1
        return 0

    def email_discover_busy(self) -> bool:
        """True when an email-discover run is inflight or queued."""
        if self._inflight_kinds.get("email_discover", 0) > 0:
            return True
        return any(item.kind == "email_discover" for item in self._pending_items)

    def email_discover_queue_depth(self) -> int:
        """Count of email-discover items waiting (excludes currently running)."""
        return sum(
            1 for item in self._pending_items if item.kind == "email_discover"
        )

    async def start(self) -> None:
        if self._worker_task is None:
            s = get_settings()
            self._general_sem = asyncio.Semaphore(s.gateway_launch_max_inflight)
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    async def launch(
        self,
        start_fn: StartFn,
        *,
        session_id: str,
        dedup_key: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> LaunchResult:
        """Enqueue or run immediately depending on capacity and settings."""
        s = get_settings()
        if not s.gateway_launch_queue_enabled:
            run = await start_fn()
            perf.launch_started_total += 1
            return LaunchResult(run=run)

        await self.start()
        resolved_kind = kind or self._kind_from_session(session_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[LaunchResult] = loop.create_future()
        async with self._lock:
            if dedup_key:
                for pending in self._pending_items:
                    if pending.dedup_key == dedup_key and not pending.future.done():
                        return await pending.future
            self._seq += 1
            seq = self._seq
            item = _QueueItem(
                priority=self._priority_for(resolved_kind),
                seq=seq,
                kind=resolved_kind,
                session_id=session_id,
                dedup_key=dedup_key,
                start_fn=start_fn,
                future=fut,
            )
            self._pending_items.append(item)
            await self._queue.put(item)
            perf.run_queue_depth = len(self._pending_items)
            perf.launch_queued_total += 1
            position = self._queue_position_for(item)

        return await fut

    def _queue_position_for(self, item: _QueueItem) -> int:
        """1-based position among same-kind waiters."""
        same_kind = [i for i in self._pending_items if i.kind == item.kind]
        same_kind.sort(key=lambda i: (i.priority, i.seq))
        for idx, pending in enumerate(same_kind, start=1):
            if pending.seq == item.seq:
                return idx
        return len(same_kind)

    async def _acquire_kind(self, kind: str) -> None:
        assert self._general_sem is not None
        if kind == "email_discover":
            await self._email_sem.acquire()
        elif kind == "recovery" and get_settings().recovery_launch_serial:
            await self._recovery_sem.acquire()
        else:
            await self._general_sem.acquire()
        async with self._lock:
            self._inflight_kinds[kind] = self._inflight_kinds.get(kind, 0) + 1
            perf.run_queue_inflight = sum(self._inflight_kinds.values())

    def _release_kind(self, kind: str) -> None:
        assert self._general_sem is not None
        if kind == "email_discover":
            self._email_sem.release()
        elif kind == "recovery" and get_settings().recovery_launch_serial:
            self._recovery_sem.release()
        else:
            self._general_sem.release()

    async def _start_with_retry(self, start_fn: StartFn) -> dict[str, Any]:
        """Retry gateway 429 inside the worker before failing the waiter."""
        last_exc: GatewayError | None = None
        for attempt in range(_CONCURRENCY_RETRIES + 1):
            try:
                return await start_fn()
            except GatewayError as exc:
                last_exc = exc
                if is_gateway_concurrency_limit(exc) and attempt < _CONCURRENCY_RETRIES:
                    perf.launch_429_total += 1
                    await asyncio.sleep(
                        min(_CONCURRENCY_RETRY_BASE_SEC * (attempt + 1), 15.0)
                    )
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise GatewayError(502, "launch queue start failed without detail")

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            async with self._lock:
                if item in self._pending_items:
                    self._pending_items.remove(item)
                perf.run_queue_depth = len(self._pending_items)
            position = self._queue_position_for(item)
            enqueue_at = asyncio.get_event_loop().time()
            await self._acquire_kind(item.kind)
            try:
                if _bridge_health_fn is not None:
                    healthy = False
                    for attempt in range(_BRIDGE_HEALTH_RETRIES):
                        if await _bridge_health_fn():
                            healthy = True
                            break
                        if attempt + 1 < _BRIDGE_HEALTH_RETRIES:
                            await asyncio.sleep(
                                _BRIDGE_HEALTH_RETRY_SEC * (attempt + 1),
                            )
                    if not healthy:
                        raise GatewayError(
                            502,
                            "bridge health check failed before gateway launch",
                        )
                run = await self._start_with_retry(item.start_fn)
                perf.launch_started_total += 1
                waited = asyncio.get_event_loop().time() - enqueue_at
                if not item.future.done():
                    item.future.set_result(
                        LaunchResult(
                            run=run,
                            queued=waited > 0.05 or position > 1,
                            queue_position=position,
                            waited_sec=waited,
                        )
                    )
            except GatewayError as exc:
                if is_gateway_concurrency_limit(exc):
                    perf.launch_429_total += 1
                if not item.future.done():
                    item.future.set_exception(exc)
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._release_kind(item.kind)
                async with self._lock:
                    self._inflight_kinds[item.kind] = max(
                        0, self._inflight_kinds.get(item.kind, 1) - 1,
                    )
                    perf.run_queue_inflight = sum(self._inflight_kinds.values())
                self._queue.task_done()

    def snapshot(self) -> dict[str, Any]:
        return {
            "queue_depth": len(self._pending_items),
            "inflight": dict(self._inflight_kinds),
            "email_discover_busy": self.email_discover_busy(),
            "email_discover_queued": self.email_discover_queue_depth(),
        }


launch_queue = RunLaunchQueue()


def new_pending_run_id() -> str:
    """Placeholder run_id for TOCTOU-safe inflight registration."""
    return f"pending:{uuid.uuid4().hex[:12]}"
