"""Unified Gmail background worker — serializes inbound + SENT ticks.

One asyncio loop wakes every few seconds and runs due tasks **in order**
(inbound first, then SENT) so Gmail API / CAL writes do not overlap across
the two schedulers. Per-path intervals and enable flags are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import gmail_inbound_poller
from . import gmail_poller

log = logging.getLogger(__name__)

_STATE_DIR = Path(
    os.environ.get(
        "KOL_OPS_BRIDGE_STATE_DIR",
        str(Path.home() / ".hermes" / "kol-ops-bridge"),
    )
)
_STATE_PATH = _STATE_DIR / "gmail_worker.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _wake_interval_sec() -> float:
    return max(2.0, float(os.environ.get("KOL_OPS_GMAIL_WORKER_WAKE_SEC", "5")))


def parallel_mode_enabled() -> bool:
    """When true, ``serve.py`` runs legacy parallel pollers instead of this worker."""
    return os.environ.get("KOL_OPS_GMAIL_WORKER_PARALLEL", "0") == "1"


def _parallel_mode() -> bool:
    return parallel_mode_enabled()


def _sent_disabled() -> bool:
    return os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_POLLER") == "1"


def _inbound_disabled() -> bool:
    return os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER") == "1"


def _load_worker_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_worker_state(state: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def _monotonic_last(state: dict[str, Any], key: str) -> float:
    try:
        return float(state.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def get_status() -> dict[str, Any]:
    """Combined worker status for ops dashboards."""
    worker = _load_worker_state()
    inbound = gmail_inbound_poller.get_status()
    from .inbound_reply import INBOUND_MODULE_VERSION

    return {
        "coordinator": "gmail_worker",
        "inbound_module_version": INBOUND_MODULE_VERSION,
        "bridge_port": inbound.get("bridge_port", "in_process"),
        "parallel_mode": _parallel_mode(),
        "sent_disabled": _sent_disabled(),
        "inbound_disabled": _inbound_disabled(),
        "wake_interval_sec": _wake_interval_sec(),
        "sent_interval_sec": gmail_poller.sent_interval_sec(),
        "sent_envs": list(gmail_poller.sent_reconcile_envs()),
        "last_inbound_tick_at": worker.get("last_inbound_tick_at"),
        "last_sent_tick_at": worker.get("last_sent_tick_at"),
        "last_cycle_at": worker.get("last_cycle_at"),
        "last_cycle_elapsed_ms": worker.get("last_cycle_elapsed_ms"),
        "inbound": inbound,
    }


def _inbound_due(*, last_mono: float, interval_sec: int, now_mono: float) -> bool:
    return last_mono <= 0.0 or (now_mono - last_mono) >= float(interval_sec)


def _sent_due(*, last_mono: float, interval_sec: int, now_mono: float) -> bool:
    return last_mono <= 0.0 or (now_mono - last_mono) >= float(interval_sec)


async def _run_inbound_if_due(
    worker_state: dict[str, Any],
    *,
    now_mono: float,
) -> tuple[dict[str, Any], bool]:
    if _inbound_disabled():
        return worker_state, False
    inbound_cfg = gmail_inbound_poller.load_state()
    if not inbound_cfg.get("enabled"):
        return worker_state, False
    interval = max(15, int(inbound_cfg.get("interval") or 60))
    last = _monotonic_last(worker_state, "last_inbound_mono")
    if not _inbound_due(last_mono=last, interval_sec=interval, now_mono=now_mono):
        return worker_state, False
    await gmail_inbound_poller.run_tick_async()
    worker_state["last_inbound_mono"] = time.monotonic()
    worker_state["last_inbound_tick_at"] = _now()
    return worker_state, True


async def _run_sent_if_due(
    worker_state: dict[str, Any],
    *,
    now_mono: float,
) -> tuple[dict[str, Any], bool]:
    if _sent_disabled():
        return worker_state, False
    interval = gmail_poller.sent_interval_sec()
    last = _monotonic_last(worker_state, "last_sent_mono")
    if not _sent_due(last_mono=last, interval_sec=interval, now_mono=now_mono):
        return worker_state, False
    count = await gmail_poller.run_sent_tick_async()
    worker_state["last_sent_mono"] = time.monotonic()
    worker_state["last_sent_tick_at"] = _now()
    worker_state["last_sent_reconciled_count"] = count
    return worker_state, True


async def run_forever() -> None:
    """Single coordinator loop for bridge ``serve.py``."""
    if _parallel_mode():
        log.info("[gmail_worker] parallel mode enabled — coordinator idle")
        while True:
            await asyncio.sleep(3600)

    if _sent_disabled() and _inbound_disabled():
        log.info("[gmail_worker] inbound + SENT both disabled — coordinator idle")
        while True:
            await asyncio.sleep(3600)

    wake = _wake_interval_sec()
    log.info(
        "[gmail_worker] coordinator started wake=%ss sent_interval=%ss",
        wake,
        gmail_poller.sent_interval_sec(),
    )

    while True:
        t0 = time.perf_counter()
        now_mono = time.monotonic()
        worker_state = _load_worker_state()
        ran_inbound = False
        ran_sent = False

        worker_state, ran_inbound = await _run_inbound_if_due(worker_state, now_mono=now_mono)
        worker_state, ran_sent = await _run_sent_if_due(worker_state, now_mono=now_mono)

        if ran_inbound or ran_sent:
            worker_state["last_cycle_at"] = _now()
            worker_state["last_cycle_elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            _save_worker_state(worker_state)
            log.info(
                "[gmail_worker] cycle inbound=%s sent=%s elapsed_ms=%s",
                ran_inbound,
                ran_sent,
                worker_state["last_cycle_elapsed_ms"],
            )

        await asyncio.sleep(wake)
