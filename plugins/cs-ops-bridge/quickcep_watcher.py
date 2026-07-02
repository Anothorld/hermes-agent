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
from .operator_send_reconcile import reconcile_operator_sent_once

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


def _truthy_env(key: str, *, default: bool) -> bool:
    """Read a boolean env var with default."""
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")

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


def _load_quickcep_credentials_from_profile() -> None:
    """Ensure QUICKCEP_* are in os.environ (profile .env), even when empty placeholders exist."""
    if os.environ.get("QUICKCEP_EMAIL") and os.environ.get("QUICKCEP_PASSWORD"):
        return
    try:
        from profile_refs import cs_profile_dir

        env_path = cs_profile_dir() / ".env"
        if not env_path.exists():
            return
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key not in ("QUICKCEP_EMAIL", "QUICKCEP_PASSWORD"):
                continue
            val = val.strip().strip("'").strip('"')
            if val and not os.environ.get(key):
                os.environ[key] = val
    except Exception as exc:
        log.debug("could not load QUICKCEP credentials from profile .env: %s", exc)


def _patch_quickcep_login_env() -> None:
    """SIO monitor calls get_valid_token() with no args; fall back to profile .env credentials."""
    _load_quickcep_credentials_from_profile()
    scripts = _quickcep_scripts_dir() / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import quickcep_login  # type: ignore

    if getattr(quickcep_login, "_cs_bridge_env_login", False):
        return

    original = quickcep_login.get_valid_token

    def _get_valid_token_with_env(email=None, password=None):
        eff_email = (email or os.environ.get("QUICKCEP_EMAIL") or "").strip() or None
        eff_password = (password or os.environ.get("QUICKCEP_PASSWORD") or "").strip() or None
        had_cached = bool((quickcep_login.load_token() or {}).get("jwt"))
        try:
            token = original(email=eff_email, password=eff_password)
            if had_cached and eff_email:
                log.info("QuickCEP token refreshed via re-login")
            return token
        except ValueError as exc:
            msg = str(exc)
            if "No valid cached token" in msg:
                if not eff_email or not eff_password:
                    log.warning(
                        "QuickCEP re-login skipped: cached JWT invalid and "
                        "QUICKCEP_EMAIL/QUICKCEP_PASSWORD missing from process env"
                    )
                else:
                    log.warning(
                        "QuickCEP cached JWT invalid but re-login was not attempted (%s)",
                        msg,
                    )
            elif msg.startswith("Login failed"):
                acct = eff_email or "<unknown>"
                log.warning("QuickCEP re-login failed for %s: %s", acct, msg)
            raise

    quickcep_login.get_valid_token = _get_valid_token_with_env  # type: ignore[assignment]
    quickcep_login._cs_bridge_env_login = True


def _rebind_sio_get_valid_token(sio_module: Any) -> None:
    """Rebind SIO module global used by ``connect()`` — ``from import`` binds too early for patches."""
    import quickcep_login  # type: ignore

    sio_module.get_valid_token = quickcep_login.get_valid_token


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
            log.info(
                "operator send skipped session=%s reason=%s",
                info.get("chatSubSessionId"),
                result.get("reason"),
            )
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


def _visitor_name(visitor_info: Any) -> Optional[str]:
    """Best-effort display name from a QuickCEP visitorInfo dict."""
    if not isinstance(visitor_info, dict):
        return None
    for key in ("firstName", "lastName", "nickname", "name"):
        val = str(visitor_info.get(key) or "").strip()
        if val:
            return val
    return None


def _visitor_locale(visitor_info: Any) -> Optional[str]:
    if not isinstance(visitor_info, dict):
        return None
    val = str(visitor_info.get("country") or visitor_info.get("locale") or "").strip()
    return val or None


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

    email = info.get("email")
    if not email and isinstance(info.get("visitorInfo"), dict):
        email = info["visitorInfo"].get("email")

    # ── Internal email blocklist ──────────────────────────────────
    # Skip processing when the sender (customer email) or any
    # recipient address matches an internal Povison mailbox.
    _INTERNAL_EMAIL_BLOCKLIST = frozenset({
        "chenyaozhuang@povison-inc.com",
        "liujinli@povison-inc.com",
        "logistics@povison-inc.com",
    })
    email_lower = str(email or "").lower().strip()
    if email_lower in _INTERNAL_EMAIL_BLOCKLIST:
        log.info(
            "skip launch session %s internal_email_blocklist sender=%s",
            session_id,
            email_lower,
        )
        return None

    gate = check_intent_gate(
        session_id,
        info.get("intentionTags"),
        customer_email=str(email) if email else None,
        env=_ENV,
    )
    if not gate.allowed:
        log.info(
            "skip launch session %s intent_gate=%s tags=%s",
            session_id,
            gate.reason,
            list(gate.tags) or None,
        )
        return None

    # Skip sessions already assigned to human operators (unless AI is actively processing)
    if _truthy_env("CS_OPS_SKIP_ASSIGNED_SESSIONS", default=True):
        existing_sess = cal.get_session(quickcep_session_id=session_id, env=_ENV)
        ai_busy = existing_sess and str(existing_sess.get("status") or "") not in ("pending", "failed", "")
        if not ai_busy:
            op_ids = info.get("operatorIds")
            if op_ids:
                log.info(
                    "skip launch session %s assigned_to_operators=%s",
                    session_id,
                    op_ids,
                )
                return None

    result = cal.enqueue_session(
        quickcep_session_id=session_id,
        chat_session_id=str(info.get("chatSessionId") or "") or None,
        customer_email=email,
        message_id=message_id,
        env=_ENV,
        email_subject=(info.get("email_subject") or None),
        last_message_preview=(info.get("content_preview") or None),
        intention_tags=(
            list(info["intentionTags"]) if info.get("intentionTags") else None
        ),
        customer_name=_visitor_name(info.get("visitorInfo")),
        locale=_visitor_locale(info.get("visitorInfo")),
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
    try:
        _patch_quickcep_login_env()
        sys.path.insert(0, str(scripts))
        import quickcep_sio_email_monitor as sio_module  # type: ignore

        _rebind_sio_get_valid_token(sio_module)
        QuickCEPSioMonitor = sio_module.QuickCEPSioMonitor
        on_new_email = sio_module.on_new_email

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


def _quickcep_subprocess_env() -> dict[str, str]:
    """Subprocess env with profile QUICKCEP credentials when the parent process lacks them."""
    _load_quickcep_credentials_from_profile()
    return {k: v for k, v in os.environ.items() if isinstance(v, str)}


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
        env=_quickcep_subprocess_env(),
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
            "operatorIds": row.get("operatorIds"),
            "visitorInfo": vi,
        }
        if not inbound_payload_is_email(info):
            continue
        if _launch_for_message(info):
            launched += 1
    op_sync = reconcile_operator_sent_once(env=_ENV)
    state = {
        "last_run": time.time(),
        "launched": launched,
        "skipped_busy": skipped_busy,
        "seen": len(sessions),
        "sio_backoff_sec": _sio_backoff_sec,
        "operator_sent_synced": op_sync.get("synced", 0),
        "operator_sent_checked": op_sync.get("checked", 0),
        "escalation_repair_checked": op_sync.get("escalation_repair_checked", 0),
        "escalation_repair_closed": op_sync.get("escalation_repair_closed", 0),
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
