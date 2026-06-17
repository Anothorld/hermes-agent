"""QuickCEP inbound watcher — Socket.io primary, REST reconcile fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import cal
from .gateway_client import GatewayClient
from .session_handoff import handle_operator_send, apply_handoff

log = logging.getLogger(__name__)

_DEFAULT_SKILL_DIR = Path.home() / ".hermes/profiles/povison-cs/skills/social-media/quickcep"
_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
_stop_event = threading.Event()
_sio_backoff_sec = 5.0


def _quickcep_scripts_dir() -> Path:
    return Path(os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", str(_DEFAULT_SKILL_DIR)))


def _patch_sio_monitor_for_operator_send(monitor_cls: type) -> None:
    """Extend profile SIO monitor to invoke operator-send callbacks without editing profile files."""
    if getattr(monitor_cls, "_cs_bridge_operator_patch", False):
        return
    original = monitor_cls._handle

    def _patched_handle(self, name: str, payload: Any) -> None:
        original(self, name, payload)
        is_email = isinstance(payload, dict) and payload.get("channel") == "email"
        if name == "operatorSendMsg" and is_email:
            try:
                info = self._extract(payload)
                _on_operator_send(info)
            except Exception as exc:
                log.warning("operatorSendMsg handler error: %s", exc)

    monitor_cls._handle = _patched_handle  # type: ignore[method-assign]
    monitor_cls._cs_bridge_operator_patch = True  # type: ignore[attr-defined]


def _on_operator_send(info: dict[str, Any]) -> None:
    try:
        result = handle_operator_send(info, env=_ENV)
        if result.get("skipped"):
            log.debug("operator send skipped session=%s reason=%s", info.get("chatSubSessionId"), result.get("reason"))
        elif result.get("ok"):
            log.info("operator send handoff ok session=%s", info.get("chatSubSessionId"))
        else:
            log.warning("operator send handoff partial/fail session=%s: %s", info.get("chatSubSessionId"), result)
    except Exception as exc:
        log.exception("operator send handoff error: %s", exc)


def _handoff_followup_while_busy(session_id: str, message_id: str) -> None:
    try:
        apply_handoff(
            quickcep_session_id=session_id,
            phase="followup_while_busy",
            env=_ENV,
            context={
                "customer_need": "客户在本轮 AI 处理中追加消息",
                "actions_taken": f"已记录 message_id={message_id}，未重复 launch",
                "follow_up": "当前 run 完成后处理最新上下文",
                "operator_hint": "客户追加了消息",
            },
            skip_quickcep=os.environ.get("CS_OPS_HANDOFF_SKIP_QUICKCEP", "").lower() in ("1", "true"),
        )
    except Exception as exc:
        log.warning("followup handoff failed session=%s: %s", session_id, exc)


def _launch_for_message(info: dict[str, Any]) -> Optional[str]:
    session_id = str(info.get("chatSubSessionId") or "")
    message_id = str(info.get("id") or info.get("lastMsgTime") or time.time())
    if not session_id:
        return None
    email = info.get("email")
    if not email and isinstance(info.get("visitorInfo"), dict):
        email = info["visitorInfo"].get("email")
    result = cal.enqueue_session(
        quickcep_session_id=session_id,
        chat_session_id=str(info.get("chatSessionId") or "") or None,
        customer_email=email,
        message_id=message_id,
        env=_ENV,
    )
    if result.get("deduped"):
        log.info("deduped session %s message %s", session_id, message_id)
        return None
    if not result.get("should_launch", True):
        log.info(
            "skip launch session %s status=%s (busy)",
            session_id,
            (result.get("session") or {}).get("status"),
        )
        _handoff_followup_while_busy(session_id, message_id)
        return None
    cal.update_session_status(session_row_id=result["session"]["id"], status="processing")
    gw = GatewayClient.from_env()
    outcome = gw.start_process_run(
        quickcep_session_id=session_id,
        env=_ENV,
        message_id=message_id,
    )
    if outcome.run_id:
        log.info("launched run %s for session %s", outcome.run_id, session_id)
        return outcome.run_id
    if outcome.dedup_skipped:
        log.info("launch dedup skip session %s message %s", session_id, message_id)
        return None
    cal.update_session_status(session_row_id=result["session"]["id"], status="failed")
    log.error("launch failed for session %s message %s", session_id, message_id)
    try:
        apply_handoff(
            quickcep_session_id=session_id,
            phase="failed",
            env=_ENV,
            context={
                "error": "gateway launch failed",
                "actions_taken": "watcher launch returned no run_id",
                "follow_up": "Console relaunch 或检查 gateway",
                "operator_hint": "AI 未启动，需人工处理",
            },
            chat_session_id=str(info.get("chatSessionId") or "") or None,
            skip_quickcep=os.environ.get("CS_OPS_HANDOFF_SKIP_QUICKCEP", "").lower() in ("1", "true"),
        )
    except Exception as exc:
        log.warning("failed handoff after launch error session=%s: %s", session_id, exc)
    return None


def run_sio_loop() -> None:
    global _sio_backoff_sec
    scripts = _quickcep_scripts_dir() / "scripts"
    monitor_path = scripts / "quickcep_sio_email_monitor.py"
    if not monitor_path.exists():
        log.error("QuickCEP SIO monitor not found: %s", monitor_path)
        return
    sys.path.insert(0, str(scripts))
    try:
        from quickcep_sio_email_monitor import QuickCEPSioMonitor, on_new_email  # type: ignore

        _patch_sio_monitor_for_operator_send(QuickCEPSioMonitor)

        @on_new_email
        def _cb(info: dict[str, Any]) -> None:
            _launch_for_message(info)

        monitor = QuickCEPSioMonitor()
        while not _stop_event.is_set():
            try:
                monitor.connect()
                _sio_backoff_sec = 5.0
                while not _stop_event.is_set():
                    if not monitor.poll_once():
                        time.sleep(5)
                        monitor.connect()
                    time.sleep(0.5)
            except Exception as exc:
                log.warning("SIO reconnect after error: %s (backoff %.0fs)", exc, _sio_backoff_sec)
                time.sleep(_sio_backoff_sec)
                _sio_backoff_sec = min(_sio_backoff_sec * 2, 120)
    except Exception as exc:
        log.exception("SIO watcher failed: %s", exc)


def run_rest_reconcile_once() -> dict[str, Any]:
    cli = _quickcep_scripts_dir() / "scripts" / "quickcep_cli.py"
    if not cli.exists():
        return {"error": "quickcep_cli not found", "launched": 0}
    proc = subprocess.run(
        [sys.executable, str(cli), "sessions", "--email-only", "--unread-only", "--compact"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_quickcep_scripts_dir()),
    )
    if proc.returncode != 0:
        return {"error": proc.stderr or proc.stdout, "launched": 0}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "invalid json from quickcep_cli", "launched": 0}
    sessions = data.get("sessions", []) if isinstance(data, dict) else data
    launched = 0
    for row in sessions:
        sid = str(row.get("id") or "")
        if not sid:
            continue
        last_msg = str(row.get("lastMsgTime") or sid)
        unread = str(row.get("unreadNum") or "0")
        msg_id = f"{last_msg}:{unread}"
        info = {
            "chatSubSessionId": sid,
            "chatSessionId": row.get("chatSessionId"),
            "id": msg_id,
            "email": row.get("email"),
        }
        if _launch_for_message(info):
            launched += 1
    state = {"last_run": time.time(), "launched": launched, "seen": len(sessions), "sio_backoff_sec": _sio_backoff_sec}
    cal.set_poller_state("quickcep_watcher", state)
    return state


def request_stop() -> None:
    _stop_event.set()


async def start_background() -> None:
    rest_interval = int(os.environ.get("CS_OPS_QUICKCEP_REST_INTERVAL_SEC", "60"))
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, run_sio_loop)
    try:
        while True:
            try:
                run_rest_reconcile_once()
            except Exception as exc:
                log.warning("REST reconcile error: %s", exc)
            await asyncio.sleep(rest_interval)
    finally:
        request_stop()
