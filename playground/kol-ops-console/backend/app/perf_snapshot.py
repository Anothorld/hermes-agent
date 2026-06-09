"""In-process performance counters for operator/admin dashboards."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PerfSnapshot:
    """Mutable counters updated by hot paths (queue, watcher, hub)."""

    run_queue_depth: int = 0
    run_queue_inflight: int = 0
    gateway_drain_tasks: int = 0
    approval_watcher_sse_subs: int = 0
    approval_watcher_mode: str = "sse_per_run"
    ws_clients: int = 0
    reconciler_runs_total: int = 0
    reconciler_last_duration_ms: float = 0.0
    reconciler_last_at: float = 0.0
    gateway_get_run_cache_hits: int = 0
    gateway_get_run_cache_misses: int = 0
    launch_queued_total: int = 0
    launch_started_total: int = 0
    launch_429_total: int = 0
    open_gateway_sse_count: int = 0
    slow_api_samples: list[dict[str, Any]] = field(default_factory=list)

    def record_slow_api(
        self,
        *,
        method: str,
        path: str,
        status: str | int,
        duration_ms: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "at": time.time(),
        }
        if extra:
            entry.update(extra)
        self.slow_api_samples.append(entry)
        if len(self.slow_api_samples) > 100:
            self.slow_api_samples = self.slow_api_samples[-100:]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_queue_depth": self.run_queue_depth,
            "run_queue_inflight": self.run_queue_inflight,
            "gateway_drain_tasks": self.gateway_drain_tasks,
            "approval_watcher_sse_subs": self.approval_watcher_sse_subs,
            "approval_watcher_mode": self.approval_watcher_mode,
            "ws_clients": self.ws_clients,
            "reconciler_runs_total": self.reconciler_runs_total,
            "reconciler_last_duration_ms": self.reconciler_last_duration_ms,
            "reconciler_last_at": self.reconciler_last_at,
            "gateway_get_run_cache": {
                "hits": self.gateway_get_run_cache_hits,
                "misses": self.gateway_get_run_cache_misses,
            },
            "launch": {
                "queued_total": self.launch_queued_total,
                "started_total": self.launch_started_total,
                "429_total": self.launch_429_total,
            },
            "open_gateway_sse_count": self.open_gateway_sse_count,
            "slow_api_recent": list(self.slow_api_samples[-20:]),
        }


perf = PerfSnapshot()
