"""Manage the KOL Gmail reply watcher daemon."""

from __future__ import annotations

import datetime as _dt
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..config import get_settings
from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, require_role

router = APIRouter(prefix="/reply-watcher", tags=["reply-watcher"])

EnvName = Literal["TEST", "LIVE"]

_STATE_DIR = Path.home() / ".hermes/kol-ops-console"
_STATE_PATH = _STATE_DIR / "reply_watcher.json"


class WatcherStartBody(BaseModel):
    env: EnvName = "TEST"
    interval: int = Field(default=60, ge=15, le=3600)
    lookback_days: int = Field(default=3, ge=1, le=30)
    max_results: int = Field(default=50, ge=1, le=500)


class SentReconcileBody(BaseModel):
    env: EnvName = "TEST"
    lookback_days: int = Field(default=7, ge=1, le=30)
    max_results: int = Field(default=100, ge=1, le=500)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _python_path() -> Path:
    return _repo_root() / "venv/bin/python"


def _script_path() -> Path:
    return _repo_root() / "plugins/kol-ops-bridge/scripts/kol_reply_dispatcher.py"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load_state() -> dict[str, Any] | None:
    try:
        if not _STATE_PATH.exists():
            return None
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(state: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def _pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # `os.kill(pid, 0)` succeeds for zombies — the kernel still has the
    # entry until the parent reaps it. Treat zombies as not running so
    # stop/restart isn't tricked into trying to signal a dead process.
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "state="],
            capture_output=True, text=True, timeout=2,
        )
        if (out.stdout or "").strip().startswith("Z"):
            return False
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def _status() -> dict[str, Any]:
    state = _load_state() or {}
    pid = state.get("pid")
    running = _pid_running(pid if isinstance(pid, int) else None)
    return {
        "running": running,
        "pid": pid if running else None,
        "env": state.get("env") if running else state.get("env"),
        "interval": state.get("interval"),
        "lookback_days": state.get("lookback_days"),
        "max_results": state.get("max_results"),
        "started_at": state.get("started_at") if running else None,
        "stopped_at": state.get("stopped_at") if not running else None,
        "log_path": state.get("log_path"),
        "command": state.get("command"),
        "state_path": str(_STATE_PATH),
    }


def _build_env() -> dict[str, str]:
    settings = get_settings()
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(Path.home() / ".hermes/profiles/kol-orchestrator"))
    env["HERMES_KOL_OPS_BRIDGE_BASE"] = settings.bridge_base
    if settings.bridge_key:
        env["HERMES_KOL_OPS_BRIDGE_KEY"] = settings.bridge_key
    env["HERMES_GATEWAY_BASE"] = settings.gateway_base
    if settings.gateway_key:
        env["HERMES_GATEWAY_KEY"] = settings.gateway_key
    return env


def _start(body: WatcherStartBody) -> dict[str, Any]:
    current = _status()
    if current["running"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"reply watcher already running in {current.get('env')} (pid={current.get('pid')}); use restart to switch mode",
        )
    python = _python_path()
    script = _script_path()
    if not python.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"python not found: {python}")
    if not script.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"script not found: {script}")
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _STATE_DIR / f"reply_watcher_{body.env.lower()}.log"
    command = [
        str(python),
        str(script),
        "--env", body.env,
        "--watch",
        "--interval", str(body.interval),
        "--lookback-days", str(body.lookback_days),
        "--max-results", str(body.max_results),
    ]
    log_fh = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(_repo_root()),
            env=_build_env(),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_fh.close()
    state = {
        "pid": proc.pid,
        "env": body.env,
        "interval": body.interval,
        "lookback_days": body.lookback_days,
        "max_results": body.max_results,
        "started_at": _now(),
        "stopped_at": None,
        "log_path": str(log_path),
        "command": command,
    }
    _save_state(state)
    return _status()


def _stop() -> dict[str, Any]:
    state = _load_state() or {}
    pid = state.get("pid")
    if isinstance(pid, int) and _pid_running(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # EPERM here typically means the pgid no longer has a living
            # leader (zombie / orphaned). Nothing to signal; fall through
            # and mark stopped so restart can spawn a fresh watcher.
            pass
    state["stopped_at"] = _now()
    _save_state(state)
    return _status()


@router.get("/status")
def status_view(_: Annotated[dict, Depends(current_user)]) -> dict[str, Any]:
    return _status()


@router.post("/start")
def start(
    body: WatcherStartBody,
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    return _start(body)


@router.post("/stop")
def stop(_: Annotated[dict, Depends(require_role("owner", "operator"))]) -> dict[str, Any]:
    return _stop()


@router.post("/restart")
def restart(
    body: WatcherStartBody,
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    _stop()
    return _start(body)


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