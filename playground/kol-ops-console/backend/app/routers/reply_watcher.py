"""Manage the KOL Gmail inbound reply watcher (bridge-integrated worker).

Inbound INBOX polling runs inside ``kol-ops-bridge`` ``serve.py`` instead of a
Console subprocess. This router is a thin operator API over bridge
``/gmail/inbound-poller/*`` endpoints. SENT reconcile remains on
``POST /reply-watcher/reconcile-sent`` → bridge ``/gmail/reconcile-sent``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import signal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, require_role

router = APIRouter(prefix="/reply-watcher", tags=["reply-watcher"])

EnvName = Literal["TEST", "LIVE"]

_LEGACY_STATE_DIR = Path.home() / ".hermes/kol-ops-console"
_LEGACY_STATE_PATH = _LEGACY_STATE_DIR / "reply_watcher.json"


class WatcherStartBody(BaseModel):
    env: EnvName = "TEST"
    interval: int = Field(default=60, ge=15, le=3600)
    lookback_days: int = Field(default=3, ge=1, le=30)
    max_results: int = Field(default=50, ge=1, le=500)


class SentReconcileBody(BaseModel):
    env: EnvName = "LIVE"
    lookback_days: int = Field(default=7, ge=1, le=30)
    max_results: int = Field(default=100, ge=1, le=500)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load_legacy_state() -> dict[str, Any] | None:
    try:
        if not _LEGACY_STATE_PATH.exists():
            return None
        data = json.loads(_LEGACY_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_legacy_state(state: dict[str, Any]) -> None:
    _LEGACY_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _LEGACY_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_LEGACY_STATE_PATH)


def _pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_legacy_subprocess() -> dict[str, Any] | None:
    """Stop pre-merge Console subprocess watcher if still running."""
    state = _load_legacy_state() or {}
    pid = state.get("pid")
    if not isinstance(pid, int) or not _pid_running(pid):
        return None
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    state["stopped_at"] = _now()
    state["legacy_migrated_at"] = _now()
    _save_legacy_state(state)
    return {"legacy_pid_stopped": pid}


def _shape_status(raw: dict[str, Any], *, legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    last_stats = raw.get("last_tick_stats")
    if not isinstance(last_stats, dict):
        last_stats = None
    out = {
        "running": bool(raw.get("running")),
        "enabled": raw.get("enabled"),
        "inbound_disabled": raw.get("inbound_disabled"),
        "pid": raw.get("pid"),
        "managed_by": raw.get("managed_by") or "bridge",
        "env": raw.get("env"),
        "interval": raw.get("interval"),
        "lookback_days": raw.get("lookback_days"),
        "max_results": raw.get("max_results"),
        "started_at": raw.get("started_at"),
        "stopped_at": raw.get("stopped_at"),
        "log_path": raw.get("log_path"),
        "command": raw.get("command"),
        "state_path": raw.get("state_path"),
        "last_tick_at": raw.get("last_tick_at"),
        "last_tick_stats": last_stats,
        "last_error": raw.get("last_error"),
    }
    if legacy:
        out["legacy_subprocess_stopped"] = legacy
    return out


async def _bridge_status(bridge: BridgeClient) -> dict[str, Any]:
    try:
        payload = await bridge.inbound_poller_status()
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "invalid bridge inbound poller status")
    return payload


@router.get("/status")
async def status_view(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
) -> dict[str, Any]:
    payload = await _bridge_status(bridge)
    return _shape_status(payload)


@router.post("/start")
async def start(
    body: WatcherStartBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    legacy = _stop_legacy_subprocess()
    try:
        payload = await bridge.inbound_poller_start(body.model_dump())
    except BridgeError as exc:
        if exc.status == 409:
            raise HTTPException(status.HTTP_409_CONFLICT, exc.detail) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    status_payload = payload if isinstance(payload, dict) else {}
    if status_payload.get("running"):
        return _shape_status(status_payload, legacy=legacy)
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "bridge failed to start inbound poller")


@router.post("/stop")
async def stop(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    legacy = _stop_legacy_subprocess()
    try:
        payload = await bridge.inbound_poller_stop()
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    status_payload = payload if isinstance(payload, dict) else {}
    return _shape_status(status_payload, legacy=legacy)


@router.post("/restart")
async def restart(
    body: WatcherStartBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    legacy = _stop_legacy_subprocess()
    try:
        payload = await bridge.inbound_poller_restart(body.model_dump())
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    status_payload = payload if isinstance(payload, dict) else {}
    return _shape_status(status_payload, legacy=legacy)


@router.post("/reconcile-sent")
async def reconcile_sent(
    body: SentReconcileBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    try:
        return await bridge.reconcile_sent(body.model_dump())
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
