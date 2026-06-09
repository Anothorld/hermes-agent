"""Inbound Gmail reply polling — runs inside bridge ``serve.py``."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

log = logging.getLogger(__name__)

EnvName = Literal["TEST", "LIVE"]

_STATE_DIR = Path(
    os.environ.get(
        "KOL_OPS_BRIDGE_STATE_DIR",
        str(Path.home() / ".hermes" / "kol-ops-bridge"),
    )
)
_STATE_PATH = _STATE_DIR / "inbound_poller.json"
_LOG_PATH = _STATE_DIR / "bridge.log"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_config() -> dict[str, Any]:
    return {
        "enabled": os.environ.get("KOL_OPS_GMAIL_INBOUND_AUTO_START", "0") == "1",
        "env": os.environ.get("KOL_OPS_GMAIL_INBOUND_ENV", "TEST").strip().upper(),
        "interval": max(15, int(os.environ.get("KOL_OPS_GMAIL_INBOUND_INTERVAL_SEC", "60"))),
        "lookback_days": max(1, int(os.environ.get("KOL_OPS_GMAIL_INBOUND_LOOKBACK_DAYS", "3"))),
        "max_results": max(1, int(os.environ.get("KOL_OPS_GMAIL_INBOUND_MAX_RESULTS", "50"))),
        "started_at": None,
        "stopped_at": None,
        "last_tick_at": None,
        "last_tick_stats": None,
        "last_error": None,
    }


def load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return _default_config()
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    if not isinstance(raw, dict):
        return _default_config()
    base = _default_config()
    base.update(raw)
    env = str(base.get("env") or "TEST").strip().upper()
    base["env"] = env if env in {"TEST", "LIVE"} else "TEST"
    base["interval"] = max(15, int(base.get("interval") or 60))
    base["lookback_days"] = max(1, int(base.get("lookback_days") or 3))
    base["max_results"] = max(1, int(base.get("max_results") or 50))
    base["enabled"] = bool(base.get("enabled"))
    return base


def save_state(state: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def get_status() -> dict[str, Any]:
    state = load_state()
    enabled = bool(state.get("enabled"))
    inbound_disabled = os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER") == "1"
    running = enabled and not inbound_disabled
    from .inbound_reply.deps import legacy_script_enabled

    bridge_port = "legacy_script" if legacy_script_enabled() else "in_process"
    return {
        "enabled": enabled,
        "inbound_disabled": inbound_disabled,
        "running": running,
        "pid": None,
        "managed_by": "bridge",
        "bridge_port": bridge_port,
        "env": state.get("env"),
        "interval": state.get("interval"),
        "lookback_days": state.get("lookback_days"),
        "max_results": state.get("max_results"),
        "started_at": state.get("started_at") if running else None,
        "stopped_at": state.get("stopped_at") if not running else None,
        "log_path": str(_LOG_PATH),
        "command": None,
        "state_path": str(_STATE_PATH),
        "last_tick_at": state.get("last_tick_at"),
        "last_tick_stats": state.get("last_tick_stats"),
        "last_error": state.get("last_error"),
    }


def configure(
    *,
    enabled: Optional[bool] = None,
    env: Optional[str] = None,
    interval: Optional[int] = None,
    lookback_days: Optional[int] = None,
    max_results: Optional[int] = None,
) -> dict[str, Any]:
    state = load_state()
    if enabled is True:
        state["enabled"] = True
        state["started_at"] = _now()
        state["stopped_at"] = None
    elif enabled is False:
        state["enabled"] = False
        state["stopped_at"] = _now()
    if env is not None:
        env_norm = str(env).strip().upper()
        if env_norm not in {"TEST", "LIVE"}:
            raise ValueError(f"invalid env: {env}")
        state["env"] = env_norm
    if interval is not None:
        state["interval"] = max(15, int(interval))
    if lookback_days is not None:
        state["lookback_days"] = max(1, int(lookback_days))
    if max_results is not None:
        state["max_results"] = max(1, int(max_results))
    save_state(state)
    return get_status()


def run_tick_sync() -> dict[str, int] | None:
    state = load_state()
    if not state.get("enabled"):
        return None

    env = str(state.get("env") or "TEST").upper()
    lookback = int(state.get("lookback_days") or 3)
    max_results = int(state.get("max_results") or 50)

    from .inbound_reply import run_once
    from .inbound_reply.deps import InboundDeps, import_legacy_run_once, legacy_script_enabled

    try:
        if legacy_script_enabled():
            legacy_run, gmail_unavailable = import_legacy_run_once()
            stats = legacy_run(
                env=env,
                lookback_days=lookback,
                max_results=max_results,
            )
        else:
            stats = run_once(
                env=env,
                lookback_days=lookback,
                max_results=max_results,
                deps=InboundDeps.in_process_default(),
            )
        state = load_state()
        state["last_tick_at"] = _now()
        state["last_tick_stats"] = stats
        state["last_error"] = None
        save_state(state)
        log.info("[gmail_inbound_poller] tick env=%s stats=%s", env, stats)
        return stats
    except Exception as exc:  # noqa: BLE001
        from .gmail_client import GmailUnavailable

        state = load_state()
        if isinstance(exc, GmailUnavailable):
            state["last_error"] = str(exc)[:500]
            save_state(state)
            log.warning("[gmail_inbound_poller] gmail unavailable: %s", exc)
            return None
        state["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
        save_state(state)
        log.exception("[gmail_inbound_poller] tick failed: %s", exc)
        return None


async def run_tick_async() -> dict[str, int] | None:
    return await asyncio.to_thread(run_tick_sync)


async def run_forever() -> None:
    if os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER") == "1":
        log.info("[gmail_inbound_poller] disabled via KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER")
        while True:
            await asyncio.sleep(3600)

    log.info("[gmail_inbound_poller] standalone loop state=%s", _STATE_PATH)
    while True:
        state = load_state()
        if not state.get("enabled"):
            await asyncio.sleep(5.0)
            continue
        await run_tick_async()
        interval = max(15, int(state.get("interval") or 60))
        await asyncio.sleep(interval)
