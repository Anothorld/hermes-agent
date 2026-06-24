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
from .email_channel import inbound_payload_is_email
from .gateway_client import GatewayClient
from .intent_gate import check_intent_gate
from .session_handoff import handle_operator_send, apply_handoff

from .profile_refs import quickcep_skill_dir

log = logging.getLogger(__name__)

_DEBUG_LOG_PATH = "/Users/arnold/agent_prj/.cursor/debug-46e7bf.log"


def _agent_debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as _f:
            _f.write(
                json.dumps(
                    {
                        "sessionId": "46e7bf",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion


_DEFAULT_SKILL_DIR = quickcep_skill_dir()
_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
_stop_event = threading.Event()
_sio_backoff_sec = 5.0

# REST reconcile only bootstraps missed first launches or retries failed rows.
# Busy statuses (processing, awaiting_expert, …) must not be re-polled: lastMsgTime
# moves when we add internal notes, which previously caused false follow-up loops.
_REST_LAUNCH_STATUSES = frozenset({"pending", "failed"})


def _quickcep_scripts_dir() -> Path:
    return Path(os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", str(_DEFAULT_SKILL_DIR)))


def rest_session_message_id(row: dict[str, Any]) -> str:
    """Stable REST reconcile dedup key — lastMsgTime only (never append unreadNum)."""
    last_msg = str(row.get("lastMsgTime") or row.get("id") or "").strip()
    if last_msg:
        return f"rest:{last_msg}"
    return f"rest:session:{row.get('id') or 'unknown'}"


def rest_reconcile_eligible(*, quickcep_session_id: str, env: str = _ENV) -> bool:
    """True when REST may enqueue/launch (new session, pending, or failed)."""
    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return True
    return str(sess.get("status") or "") in _REST_LAUNCH_STATUSES


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


def _record_followup_while_busy(*, session_id: str, message_id: str, status: str) -> None:
    """Audit-only follow-up signal — enqueue already wrote ``customer_followup_while_busy``.

    Intentionally does **not** post QuickCEP internal notes: REST/SIO dedup keys derived from
    ``lastMsgTime`` would otherwise create a feedback loop when notes bump session activity.
    """
    log.info(
        "customer follow-up while busy session=%s status=%s message_id=%s (CAL event only)",
        session_id,
        status,
        message_id,
    )


def _launch_for_message(info: dict[str, Any]) -> Optional[str]:
    session_id = str(info.get("chatSubSessionId") or "")
    message_id = str(info.get("id") or info.get("lastMsgTime") or time.time())
    if not session_id:
        return None

    if not inbound_payload_is_email(info):
        log.info(
            "skip launch session %s non_email channel=%s",
            session_id,
            info.get("channel"),
        )
        return None

    gate = check_intent_gate(session_id, info.get("intentionTags"))
    if not gate.allowed:
        log.info(
            "skip launch session %s intent_gate=%s tags=%s",
            session_id,
            gate.reason,
            list(gate.tags) or None,
        )
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
        session_status = str((result.get("session") or {}).get("status") or "")
        log.info(
            "skip launch session %s status=%s (busy)",
            session_id,
            session_status,
        )
        _record_followup_while_busy(
            session_id=session_id,
            message_id=message_id,
            status=session_status,
        )
        _agent_debug_log(
            hypothesis_id="A",
            location="quickcep_watcher.py:_launch_for_message",
            message="busy session enqueue (no QuickCEP note)",
            data={
                "session_id": session_id,
                "status": session_status,
                "message_id": message_id,
                "source": "sio_or_rest",
            },
        )
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
                "actions_taken": "未能自动处理该会话",
                "follow_up": "请人工查看客户来信并回复；如需重试可在工单列表重新处理",
                "operator_hint": "自动处理未启动，请根据客户诉求人工跟进",
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
        [sys.executable, str(cli), "sessions", "--email-only", "--unread-only", "--page-size", "100"],
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
    skipped_busy = 0
    for row in sessions:
        sid = str(row.get("id") or "")
        if not sid:
            continue
        if not rest_reconcile_eligible(quickcep_session_id=sid, env=_ENV):
            skipped_busy += 1
            busy_sess = cal.get_session(quickcep_session_id=sid, env=_ENV)
            if busy_sess and str(busy_sess.get("status") or "") == "awaiting_expert":
                _agent_debug_log(
                    hypothesis_id="B",
                    location="quickcep_watcher.py:run_rest_reconcile_once",
                    message="REST skipped awaiting_expert session",
                    data={"session_id": sid, "last_message_id": busy_sess.get("last_message_id")},
                )
            continue
        msg_id = rest_session_message_id(row)
        sess = cal.get_session(quickcep_session_id=sid, env=_ENV)
        if sess and str(sess.get("last_message_id") or "") == msg_id:
            continue
        vi = row.get("visitorInfo") if isinstance(row.get("visitorInfo"), dict) else {}
        info = {
            "chatSubSessionId": sid,
            "chatSessionId": row.get("chatSessionId"),
            "id": msg_id,
            "email": row.get("email") or vi.get("email"),
            "intentionTags": row.get("intentionTags"),
            "channel": row.get("channel") or "email",
        }
        if not inbound_payload_is_email(info):
            continue
        if _launch_for_message(info):
            launched += 1
    state = {
        "last_run": time.time(),
        "launched": launched,
        "skipped_busy": skipped_busy,
        "seen": len(sessions),
        "sio_backoff_sec": _sio_backoff_sec,
    }
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
                await loop.run_in_executor(None, run_rest_reconcile_once)
            except Exception as exc:
                log.warning("REST reconcile error: %s", exc)
            await asyncio.sleep(rest_interval)
    finally:
        request_stop()
