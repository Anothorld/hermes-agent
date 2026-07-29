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
from .ad_detector import detect_ad_from_info, parse_rest_last_msg_content, has_ad_tag, AD_TAG_ID
from .email_channel import inbound_payload_is_email
from .gateway_client import GatewayClient
from .intent_gate import check_intent_gate
from .quickcep_join import (
    join_chat_on_launch_enabled,
    join_chat_session,
    launch_join_max_attempts,
    record_join_chat_event,
)
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


# Cached QuickCEP userId of the AI/bridge system account (from .quickcep_token.json).
# Used by the "skip assigned sessions" guard so the watcher does NOT skip sessions
# assigned to the AI account itself — only those assigned to other human operators.
# ``None``  = not loaded yet; once loaded it is always a non-empty string (we never
# cache an empty result so the lookup retries until the AI token is available).
_system_operator_id_cache: Optional[str] = None


def _system_operator_id() -> str:
    """Return the bridge's own QuickCEP userId (cached), or ``""`` if unavailable.

    Reads the cached ``.quickcep_token.json`` produced by ``quickcep_login``.
    Falls back to ``CS_OPS_SYSTEM_OPERATOR_ID`` env override for deployments that
    cannot rely on the token cache. Never raises.
    """
    global _system_operator_id_cache
    if _system_operator_id_cache:
        return _system_operator_id_cache
    env_override = (os.environ.get("CS_OPS_SYSTEM_OPERATOR_ID") or "").strip()
    if env_override:
        _system_operator_id_cache = env_override
        return _system_operator_id_cache
    try:
        scripts = _quickcep_scripts_dir() / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import quickcep_login  # type: ignore

        cached = quickcep_login.load_token() or {}
        uid = str(cached.get("userId") or "").strip()
        # Only cache a non-empty result: if the token isn't cached yet at first
        # call (e.g. watcher started before AI login), we want to re-read on the
        # next inbound instead of pinning "" for the whole process lifetime.
        if uid:
            _system_operator_id_cache = uid
        return uid
    except Exception as exc:  # noqa: BLE001 — best-effort lookup
        log.debug("system operator id lookup failed: %s", exc)
        return ""

# REST reconcile bootstraps missed first launches or retries failed rows.
# Busy statuses (processing, awaiting_expert, …) must not be re-polled: lastMsgTime
# moves when we add internal notes, which previously caused false follow-up loops.
# operator_replied/reviewed are included so customer follow-ups after operator send
# are picked up by REST when SIO misses the event. The --unread-only filter and
# enqueue_session's dedup table prevent false re-launches on operator-only actions.
_REST_LAUNCH_STATUSES = frozenset({"pending", "failed", "operator_replied", "reviewed"})


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


def _enqueue_permanent_skip(
    *,
    session_id: str,
    info: dict[str, Any],
    gate: str,
    extra_payload: Optional[dict[str, Any]] = None,
) -> None:
    """Enqueue a permanent inbound skip: session → status=skipped + inbound_skipped event.

    Used by the 4 permanent skip gates (non_email, blocklist, intent_gate
    not_allowed, ad). Transient skips (no_intention_tags, assigned_operators)
    do NOT call this — they stay log-only to preserve REST reconcile retry.

    The ``force_status`` busy guard in ``enqueue_session`` ensures an in-flight
    session (processing/awaiting_expert/…) is never disrupted: only idle/new/
    already-skipped sessions get marked skipped; busy sessions keep their status
    but still receive the ``inbound_skipped`` audit event.
    """
    skip_payload = {"gate": gate}
    if extra_payload:
        skip_payload.update(extra_payload)
    try:
        cal.enqueue_session(
            quickcep_session_id=session_id,
            chat_session_id=str(info.get("chatSessionId") or "") or None,
            customer_email=info.get("email") or None,
            message_id=str(info.get("id") or ""),
            env=_ENV,
            email_subject=(info.get("email_subject") or None),
            last_message_preview=(info.get("content_preview") or None),
            force_status="skipped",
            skip_event_payload=skip_payload,
        )
    except Exception as exc:
        log.warning("inbound_skipped enqueue failed session=%s gate=%s: %s", session_id, gate, exc)


def _tag_ad_and_skip(
    *,
    session_id: str,
    info: dict[str, Any],
    reason: str,
) -> None:
    """Tag an ad/spam session with 广告, write CAL audit events, and skip processing.

    PR3: routes through ``_enqueue_permanent_skip`` (force_status=skipped) so the
    busy guard applies — a `processing` session receiving an ad follow-up no
    longer gets its status overwritten to `skipped` (the prior unconditional
    ``update_session_status`` was a latent bug that orphaned in-flight runs).
    The legacy ``ad_email_detected`` event is preserved for backward compat.
    """
    _enqueue_permanent_skip(
        session_id=session_id,
        info=info,
        gate="ad",
        extra_payload={"reason": reason, "subject": (info.get("email_subject") or "")[:200]},
    )
    # Apply the 广告 tag directly via QuickCEP CLI.
    try:
        from .session_handoff import _run_quickcep_cli
        _run_quickcep_cli(["tags-add", session_id, AD_TAG_ID])
    except Exception as exc:
        log.warning("ad tag add failed session=%s: %s", session_id, exc)
    # Legacy audit event (kept for backward compat with existing queries).
    try:
        cal.write_event(
            quickcep_session_id=session_id,
            env=_ENV,
            event_type="ad_email_detected",
            payload={"reason": reason, "subject": (info.get("email_subject") or "")[:200]},
        )
    except Exception as exc:
        log.warning("ad event write failed session=%s: %s", session_id, exc)


def _leave_quickcep_if_previously_joined(*, session_id: str) -> None:
    """Leave the QuickCEP session (unassign the AI) when the AI previously joined it.

    Used on permanent intent-gate skips (``intention_not_allowed`` / out_of_scope).
    A reopened session often passed the gate on its first message — the AI joined
    via ``join_chat_session`` — but the new follow-up reclassifies to an
    out-of-scope intent (e.g. ``order_management``). Without leaving, the session
    stays assigned to the AI account while CAL marks it ``skipped``: the AI won't
    process it, human operators can't take it, and no escalation is created —
    the case gets stuck. Leaving hands it back to the unassigned queue.

    No-op when the AI never joined this session (first-message skip), when the
    CAL row is missing, when the session is still busy (the skip did not take
    effect — ``_enqueue_permanent_skip``'s busy guard kept an in-flight status
    like ``processing``/``awaiting_expert``), when a prior skip already left
    the session (idempotent), or when the leave-chat call fails (fail-soft — the
    skip itself still stands). Never raises.
    """
    try:
        sess = cal.get_session(quickcep_session_id=session_id, env=_ENV)
    except Exception as exc:
        log.debug("leave-on-skip session lookup failed session=%s: %s", session_id, exc)
        return
    if not sess:
        return
    # Busy guard: only leave when the skip actually took effect (status=skipped).
    # _enqueue_permanent_skip's force_status busy guard keeps in-flight statuses
    # (processing/awaiting_expert/draft_ready/operator_replied/reviewed) unchanged —
    # calling leave-chat on those would kick the AI out of a session it is actively
    # working on (or one a human expert is engaged with), orphaning the gateway run.
    if str(sess.get("status") or "") != "skipped":
        log.debug(
            "leave-on-skip skipped session=%s status=%s (busy, not disrupted)",
            session_id, sess.get("status"),
        )
        return
    try:
        joined = cal.session_has_event(
            session_row_id=sess["id"], event_type="quickcep_join_chat",
        )
    except Exception as exc:
        log.debug("leave-on-skip join-event lookup failed session=%s: %s", session_id, exc)
        return
    if not joined:
        return
    # Idempotency: if a prior intent_gate_skip already left this session, don't
    # call leave-chat again on repeated out_of_scope follow-ups — the AI is
    # already out and the session is already in the human queue.
    try:
        already_left = cal.session_has_event(
            session_row_id=sess["id"], event_type="quickcep_leave_chat",
        )
    except Exception as exc:
        log.debug("leave-on-skip leave-event lookup failed session=%s: %s", session_id, exc)
        already_left = False
    if already_left:
        log.debug("leave-on-skip already left session=%s (idempotent no-op)", session_id)
        return
    try:
        from .session_handoff import _run_quickcep_cli
        from .quickcep_leave_confirm import reconcile_leave_chat_payload
        cli = _quickcep_scripts_dir() / "scripts" / "quickcep_cli.py"
        code, out, _err = _run_quickcep_cli(["leave-chat", session_id])
        # Parse the CLI payload and reconcile email-leaveChat vs live-chat_end so
        # the recorded `ok` reflects actual unassignment — exit code alone is
        # unreliable (email sessions exit 0 with `chat_end_not_confirmed` even
        # though leaveChat unassigned the session). Mirrors close_session.
        try:
            payload = json.loads(out) if out else {}
        except json.JSONDecodeError:
            payload = {"raw_stdout": (out or "")[:500]}
        payload = reconcile_leave_chat_payload(payload, cli=cli, session_id=session_id)
        ok = bool(payload.get("ok"))
        cal.write_event(
            quickcep_session_id=session_id,
            env=_ENV,
            event_type="quickcep_leave_chat",
            payload={
                "source": "intent_gate_skip",
                "ok": ok,
                "exit_code": code,
                "result_code": payload.get("result_code"),
                "error": payload.get("error"),
            },
        )
        if ok:
            log.info("intent_gate skip → leave-chat ok session=%s", session_id)
        else:
            log.warning(
                "intent_gate skip → leave-chat failed session=%s exit=%s err=%s",
                session_id, code, payload.get("error") or "<unknown>",
            )
    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.warning("intent_gate skip → leave-chat crashed session=%s: %s", session_id, exc)
        try:
            cal.write_event(
                quickcep_session_id=session_id,
                env=_ENV,
                event_type="quickcep_leave_chat",
                payload={"source": "intent_gate_skip", "ok": False, "error": str(exc)},
            )
        except Exception:
            pass


def _launch_for_message(info: dict[str, Any]) -> Optional[str]:
    session_id = str(info.get("chatSubSessionId") or "")
    message_id = str(info.get("id") or info.get("lastMsgTime") or time.time())
    if not session_id:
        return None

    # ── Global pause (下班) ────────────────────────────────────────
    # When the operator clicks 下班, the Console sets a global pause flag.
    # Skip ALL new launches (SIO + REST); in-flight runs complete naturally.
    # This prevents new AI drafts → new escalations on off-hours.
    if cal.is_globally_paused():
        log.info("skip launch session %s — globally paused (下班)", session_id)
        return None

    if not inbound_payload_is_email(info):
        log.info(
            "skip launch session %s non_email channel=%s",
            session_id,
            info.get("channel"),
        )
        _enqueue_permanent_skip(
            session_id=session_id,
            info=info,
            gate="non_email",
            extra_payload={"channel": info.get("channel")},
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
        _enqueue_permanent_skip(
            session_id=session_id,
            info=info,
            gate="blocklist",
            extra_payload={"sender": email_lower},
        )
        return None

    # ── Ad / spam detection ─────────────────────────────────────────
    # Check for unsolicited marketing, SEO, collaboration, guest-post,
    # partnership, or tariff proposal emails.  When detected, tag the
    # session with 广告 and skip AI processing entirely.
    if detect_ad_from_info(info):
        log.info(
            "ad_email_detected session=%s subject=%s",
            session_id,
            (info.get("email_subject") or "")[:80],
        )
        _tag_ad_and_skip(
            session_id=session_id,
            info=info,
            reason="advertisement keyword match",
        )
        return None

    # Detect if this is a reopened session (prior CAL record with terminal status).
    # If so, force re-classification — the customer's new message may have a different intent.
    _existing_sess = cal.get_session(quickcep_session_id=session_id, env=_ENV)
    _force_reclassify = bool(_existing_sess and str(_existing_sess.get("status") or "") in (
        "operator_replied", "reviewed", "draft_ready", "failed",
    ))

    gate = check_intent_gate(
        session_id,
        info.get("intentionTags"),
        customer_email=str(email) if email else None,
        env=_ENV,
        info=info,
        force_reclassify=_force_reclassify,
    )
    if not gate.allowed:
        log.info(
            "skip launch session %s intent_gate=%s tags=%s",
            session_id,
            gate.reason,
            list(gate.tags) or None,
        )
        # Permanent skip: intent explicitly not in allowlist. Enqueue + skipped.
        # Transient skip: no_intention_tags (QuickCEP classification pending) stays
        # log-only — enqueuing would write cs_message_dedup and break REST retry
        # when QuickCEP later assigns tags.
        if gate.reason == "intention_not_allowed" or gate.reason.startswith("intention_not_allowed"):
            _enqueue_permanent_skip(
                session_id=session_id,
                info=info,
                gate="intent_gate",
                extra_payload={"reason": gate.reason, "tags": list(gate.tags) or []},
            )
            # If the AI previously joined this QuickCEP session (e.g. a reopen
            # where the first message passed the gate), leave now so the session
            # returns to the unassigned queue instead of staying stuck on the AI
            # account in a skipped state. No-op when the AI never joined.
            _leave_quickcep_if_previously_joined(session_id=session_id)
        return None

    # Skip sessions already assigned to human operators (unless AI is actively
    # processing, or the session is assigned to the AI/system operator itself).
    #
    # The system (bridge) has its own QuickCEP operator account (userId in
    # .quickcep_token.json). Sessions assigned to that account MUST still be
    # processed by the AI — only sessions assigned to OTHER human operators are
    # skipped (a human is already handling them). This prevents cases from
    # landing in the operator queue "joined but never tagged" when QuickCEP
    # routes them to the AI account.
    if _truthy_env("CS_OPS_SKIP_ASSIGNED_SESSIONS", default=True):
        existing_sess = cal.get_session(quickcep_session_id=session_id, env=_ENV)
        ai_busy = existing_sess and str(existing_sess.get("status") or "") not in ("pending", "failed", "")
        if not ai_busy:
            op_ids = info.get("operatorIds")
            if op_ids:
                sys_op_id = _system_operator_id()
                op_id_list = [str(x) for x in (op_ids if isinstance(op_ids, list) else [op_ids])]
                # Only skip when none of the assignees is the system operator.
                if not (sys_op_id and sys_op_id in op_id_list):
                    log.info(
                        "skip launch session %s assigned_to_operators=%s (system_op=%s)",
                        session_id,
                        op_ids,
                        sys_op_id or "<none>",
                    )
                    return None
                else:
                    log.info(
                        "launch session %s assigned_to_system_operator=%s (will process)",
                        session_id,
                        op_ids,
                    )

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
    # Non-deduped inbound → a genuinely new message for this session. Drop the
    # L2 caches (messages/tags/orders) so the Console's next GET sees the new
    # content instead of a stale 15s/60s/300s entry. For brand-new sessions
    # this is a no-op (cache empty). Watcher runs in-process alongside the
    # bridge HTTP server (serve.py lifespan), so the dict invalidation is
    # visible to the API routes immediately.
    try:
        from .quickcep_live import invalidate_cache
        invalidate_cache(session_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.debug("watcher cache invalidate failed session=%s: %s", session_id, exc)
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
    # ── Launch joinChat (fail-soft) ─────────────────────────────────
    # Join the QuickCEP session as soon as the inbound email has passed all
    # gates and is confirmed for AI processing. This makes the AI account
    # visible to operators in QuickCEP during the lookup/draft phase. Failure
    # is non-fatal — the agent run proceeds and Console send-email still
    # joins as a fallback.
    if join_chat_on_launch_enabled():
        try:
            join_result = join_chat_session(
                session_id,
                max_attempts=launch_join_max_attempts(),
                raise_on_failure=False,
                source="launch",
            )
            record_join_chat_event(
                quickcep_session_id=session_id,
                join_result=join_result,
                message_id=message_id,
                env=_ENV,
            )
        except Exception as exc:
            log.warning("launch joinChat error session=%s: %s", session_id, exc)
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
    # Transient gateway failure (429/5xx/unreachable) — re-queue to ``pending``
    # instead of failing the session. The next watcher tick retries when a
    # gateway slot frees up. This is the pending-queue behavior: a full gateway
    # must never permanently fail a session that has not been processed.
    if outcome.transient:
        cal.update_session_status(session_row_id=result["session"]["id"], status="pending")
        log.warning(
            "launch requeued (transient) session=%s message=%s — back to pending",
            session_id, message_id,
        )
        try:
            cal.write_event(
                quickcep_session_id=session_id,
                env=_ENV,
                event_type="launch_requeued",
                payload={
                    "message_id": message_id,
                    "error": "gateway transient (429/5xx/unreachable)",
                    "run_id": None,
                },
            )
        except Exception as exc:
            log.warning("launch_requeued event write failed session=%s: %s", session_id, exc)
        return None
    cal.update_session_status(session_row_id=result["session"]["id"], status="failed")
    log.error("launch failed for session %s message %s", session_id, message_id)
    try:
        cal.write_event(
            quickcep_session_id=session_id,
            env=_ENV,
            event_type="launch_failed",
            payload={
                "message_id": message_id,
                "error": "gateway launch failed",
                "run_id": None,
            },
        )
    except Exception as exc:
        log.warning("launch_failed event write failed session=%s: %s", session_id, exc)
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
            # status was just set to "failed" above; force the AI-处理失败 tag
            # to be written to QuickCEP (otherwise the "session already failed"
            # skip guard would drop it — see quickcep-tags-dropped-on-closed-sessions).
            force_quickcep_tags=True,
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


_REST_MAX_PAGES = int(os.environ.get("CS_OPS_REST_MAX_PAGES", "5"))


def run_rest_reconcile_once() -> dict[str, Any]:
    cli = _quickcep_scripts_dir() / "scripts" / "quickcep_cli.py"
    if not cli.exists():
        return {"error": "quickcep_cli not found", "launched": 0}
    launched = 0
    skipped_busy = 0
    total_seen = 0
    pages_scanned = 0
    for page in range(1, _REST_MAX_PAGES + 1):
        proc = subprocess.run(
            [sys.executable, str(cli), "sessions", "--email-only", "--unread-only",
             "--page-size", "100", "--page", str(page)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_quickcep_scripts_dir()),
            env=_quickcep_subprocess_env(),
        )
        if proc.returncode != 0:
            log.warning("REST reconcile page %d failed: %s", page, proc.stderr or proc.stdout[:200])
            break
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            log.warning("REST reconcile page %d: invalid json", page)
            break
        sessions = data.get("sessions", []) if isinstance(data, dict) else data
        if not sessions:
            break
        total_seen += len(sessions)
        pages_scanned = page
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
            # Skip sessions already tagged as 广告 in QuickCEP.
            if has_ad_tag(row):
                log.info("REST skip session %s ad_tagged", sid)
                continue
            msg_id = rest_session_message_id(row)
            sess = cal.get_session(quickcep_session_id=sid, env=_ENV)
            if sess and str(sess.get("last_message_id") or "") == msg_id:
                continue
            # For reopened sessions (operator_replied/reviewed), verify the last
            # QuickCEP message is from a visitor — not a system action (close,
            # transfer, tag change) or operator note/reply that bumped lastMsgTime.
            if sess and str(sess.get("status") or "") in ("operator_replied", "reviewed"):
                last_msg_type = str(row.get("lastMsgContentType") or "")
                if last_msg_type in ("text", "internalNote"):
                    log.info(
                        "REST skip session %s — lastMsg is %s (system/operator), status=%s",
                        sid, last_msg_type, sess.get("status"),
                    )
                    continue
                if last_msg_type == "html":
                    last_owner = str(row.get("lastMsgOwnerId") or "")
                    chat_sid = str(row.get("chatSessionId") or "")
                    if last_owner and chat_sid and last_owner != chat_sid:
                        log.info(
                            "REST skip session %s — lastMsg is operator reply (ownerId=%s != chatSessionId=%s)",
                            sid, last_owner, chat_sid,
                        )
                        continue
            vi = row.get("visitorInfo") if isinstance(row.get("visitorInfo"), dict) else {}
            # Extract email_subject and content from lastMsgContent for ad detection.
            rest_subject, rest_content = parse_rest_last_msg_content(row)
            info = {
                "chatSubSessionId": sid,
                "chatSessionId": row.get("chatSessionId"),
                "id": msg_id,
                "email": row.get("email") or vi.get("email"),
                "intentionTags": row.get("intentionTags"),
                "channel": row.get("channel") or "email",
                "operatorIds": row.get("operatorIds"),
                "visitorInfo": vi,
                "email_subject": rest_subject,
                "content_preview": rest_content[:300] if rest_content else "",
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
        "seen": total_seen,
        "pages_scanned": pages_scanned,
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


# ── Re-arming: detect customer follow-ups on operator_replied/reviewed sessions ──
# Independently scans CAL for sessions in terminal statuses and checks QuickCEP
# for newer visitor messages. If found, resets the CAL status to "pending" so the
# next REST reconcile cycle picks it up. This compensates for:
#   1. SIO event loss (visitorSendMsg missed during disconnects)
#   2. --unread-only filtering out sessions where an operator joined (clearing unreadNum)
#   3. REST pagination not reaching older sessions
_REARM_STATUSES = frozenset({"operator_replied", "reviewed"})
_REARM_INTERVAL_SEC = int(os.environ.get("CS_OPS_REARM_INTERVAL_SEC", "300"))
_REARM_MAX_SESSIONS = int(os.environ.get("CS_OPS_REARM_MAX_SESSIONS", "50"))
# Only re-arm sessions updated within this many hours (avoids scanning ancient history)
_REARM_MAX_AGE_HOURS = int(os.environ.get("CS_OPS_REARM_MAX_AGE_HOURS", "168"))


def _run_quickcep_cli_json(*args: str) -> Optional[dict[str, Any]]:
    """Run quickcep_cli.py and return parsed JSON, or None on failure."""
    cli = _quickcep_scripts_dir() / "scripts" / "quickcep_cli.py"
    if not cli.exists():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(cli)] + list(args),
            capture_output=True, text=True, timeout=30,
            cwd=str(_quickcep_scripts_dir()),
            env=_quickcep_subprocess_env(),
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None


def _rest_last_msg_time(cal_last_msg_id: str) -> str:
    """Extract ``lastMsgTime`` from a REST dedup marker ``rest:{lastMsgTime}``.

    Returns empty string for non-REST markers or ``rest:session:{id}`` placeholders.
    """
    marker = (cal_last_msg_id or "").strip()
    if marker.startswith("rest:session:"):
        return ""
    if marker.startswith("rest:"):
        return marker[len("rest:") :].strip()
    return ""


def _parse_quickcep_time_to_epoch(ts: str) -> Optional[float]:
    """Parse a QuickCEP ``createTime`` (UTC+8, ``YYYY-MM-DD HH:MM:SS``) to epoch."""
    if not ts:
        return None
    try:
        from datetime import datetime, timezone, timedelta

        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        # QuickCEP server is UTC+8.
        return dt.replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
    except (ValueError, TypeError):
        return None


def _parse_utc_time_to_epoch(ts: str) -> Optional[float]:
    """Parse a CAL ``created_at`` (UTC, ``YYYY-MM-DD HH:MM:SS``) to epoch."""
    if not ts:
        return None
    try:
        from datetime import datetime, timezone

        # CAL created_at may have fractional seconds or 'Z' — normalize.
        clean = ts.rstrip("Z").split(".")[0]
        dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def is_newer_visitor_followup(
    *,
    cal_last_msg_id: str,
    visitor_msg_id: str,
    visitor_create_time: str,
    inbound_received_at: str = "",
) -> bool:
    """Return True only with positive evidence the visitor message is new vs CAL.

    REST reconcile stores ``last_message_id`` as ``rest:{lastMsgTime}``, while
    QuickCEP ``messages`` returns a native numeric/card id + ``createTime``.
    Substring id matching alone false-positives (same customer mail, different
    id formats) and wrongly re-arms ``reviewed`` / ``operator_replied`` rows.

    For non-REST markers (SIO native ids), substring matching is unreliable
    because QuickCEP may return a different visitor message (or the same one
    with a different id representation). In that case, fall back to comparing
    ``visitor_create_time`` against ``inbound_received_at`` (the CAL
    ``inbound_received`` event's ``created_at``). When either timestamp is
    missing, there is no positive evidence of a newer follow-up → do not re-arm.

    **Timezone handling**: CAL ``created_at`` is UTC (SQLite ``datetime()``
    output, e.g. ``2026-07-21 02:39:53``). QuickCEP ``createTime`` is in the
    QuickCEP server's local time (UTC+8, e.g. ``2026-07-21 10:36:55``). Direct
    string comparison would be wrong (10:36 > 02:39 even when they are the same
    instant). Both timestamps are parsed to epoch seconds before comparing.
    """
    cal_marker = (cal_last_msg_id or "").strip()
    vid = (visitor_msg_id or "").strip()
    vtime = (visitor_create_time or "").strip()

    if not cal_marker:
        return bool(vid or vtime)

    # Native / SIO id already tracked (either direction for hybrid markers).
    if vid and (vid in cal_marker or cal_marker in vid):
        return False

    rest_time = _rest_last_msg_time(cal_marker)
    if rest_time:
        # REST path: both rest_time and vtime come from QuickCEP's API and are
        # in the same timezone (UTC+8). String comparison is safe here.
        if not vtime:
            return False
        return vtime > rest_time

    # Non-REST marker whose id did not match. Previously this returned
    # ``bool(vid)``, which false-positives when QuickCEP returns a different
    # visitor message (or the same one with a different id format) — causing
    # reviewed / operator_replied sessions to be wrongly reset to pending.
    # Require positive time evidence: visitor createTime must be strictly
    # newer than the CAL inbound_received event's created_at. Missing either
    # timestamp → cannot prove newer → do not re-arm.
    baseline = (inbound_received_at or "").strip()
    if not vtime or not baseline:
        return False
    # CAL created_at is UTC; QuickCEP createTime is UTC+8. Parse both to epoch
    # before comparing — direct string comparison is wrong across timezones.
    v_epoch = _parse_quickcep_time_to_epoch(vtime)
    b_epoch = _parse_utc_time_to_epoch(baseline)
    if v_epoch is None or b_epoch is None:
        # Cannot parse → no positive evidence → do not re-arm.
        return False
    return v_epoch > b_epoch


def _rearm_check_session(sess: dict[str, Any]) -> bool:
    """Check if a CAL session has a newer visitor message in QuickCEP. Reset to pending if so.

    Returns True if the session was re-armed (status reset to pending).
    """
    sid = str(sess.get("quickcep_session_id") or "")
    if not sid:
        return False
    cal_last_msg_id = str(sess.get("last_message_id") or "")

    # Fetch the latest messages from QuickCEP to find the most recent visitor message.
    data = _run_quickcep_cli_json("messages", sid)
    if not data:
        return False
    msgs = data.get("messages", [])
    if not msgs:
        return False

    # Find the latest visitor (customer) message.
    latest_visitor_msg = None
    for m in msgs:
        if m.get("ownerType") == "visitor" and m.get("contentType") in ("html", "text"):
            latest_visitor_msg = m
            break  # messages are in reverse chronological order (page 0 = newest)

    if not latest_visitor_msg:
        return False

    visitor_msg_id = str(latest_visitor_msg.get("id") or "")
    visitor_create_time = str(latest_visitor_msg.get("createTime") or "")

    # For non-REST markers (SIO native ids), is_newer_visitor_followup needs a
    # time baseline to avoid false-positives on id format mismatch. Use the
    # CAL inbound_received event's created_at as the baseline.
    inbound_received_at = ""
    try:
        inbound_received_at = cal.latest_event_created_at(
            session_row_id=int(sess["id"]),
            event_types=("inbound_received",),
        ) or ""
    except Exception as exc:
        log.debug("rearm: inbound_received_at lookup failed session=%s: %s", sid, exc)

    if not is_newer_visitor_followup(
        cal_last_msg_id=cal_last_msg_id,
        visitor_msg_id=visitor_msg_id,
        visitor_create_time=visitor_create_time,
        inbound_received_at=inbound_received_at,
    ):
        return False

    # enqueue_session will handle the dedup check properly on the next reconcile.
    log.info(
        "rearm: session %s has new visitor msg %s (createTime=%s), resetting to pending",
        sid, visitor_msg_id, visitor_create_time,
    )

    try:
        cal.update_session_status(session_row_id=int(sess["id"]), status="pending")
        # Write a rearm event for auditability.
        cal.write_event(
            quickcep_session_id=sid,
            env=_ENV,
            event_type="rearm_operator_replied",
            payload={
                "prior_status": sess.get("status"),
                "visitor_msg_id": visitor_msg_id,
                "visitor_msg_time": visitor_create_time,
            },
        )
        return True
    except Exception as exc:
        log.warning("rearm: failed to reset session %s: %s", sid, exc)
        return False


def run_rearm_scan_once() -> dict[str, Any]:
    """Scan operator_replied/reviewed sessions for new customer follow-ups.

    Called independently from start_background on its own interval.
    Does NOT launch AI processing directly — it only resets CAL status to "pending",
    letting the next REST reconcile cycle handle the actual launch.
    """
    now = time.time()
    rearmed = 0
    checked = 0
    errors = 0

    for status in sorted(_REARM_STATUSES):
        try:
            sessions = cal.list_sessions(env=_ENV, status=status, limit=_REARM_MAX_SESSIONS)
        except Exception as exc:
            log.warning("rearm: list_sessions status=%s failed: %s", status, exc)
            errors += 1
            continue

        for sess in sessions:
            # Skip sessions older than _REARM_MAX_AGE_HOURS based on updated_at.
            updated_str = str(sess.get("updated_at") or "")
            if updated_str:
                try:
                    from datetime import datetime, timezone
                    updated_dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                    age_hours = (now - updated_dt.timestamp()) / 3600
                    if age_hours > _REARM_MAX_AGE_HOURS:
                        continue
                except Exception:
                    pass  # If we can't parse the timestamp, proceed anyway.

            checked += 1
            try:
                if _rearm_check_session(sess):
                    rearmed += 1
            except Exception as exc:
                log.warning("rearm: session %s check failed: %s", sess.get("quickcep_session_id"), exc)
                errors += 1

    state = {
        "last_run": now,
        "checked": checked,
        "rearmed": rearmed,
        "errors": errors,
    }
    cal.set_poller_state("rearm_scanner", state)
    log.info("rearm scan: checked=%d rearmed=%d errors=%d", checked, rearmed, errors)
    return state


async def start_background() -> None:
    rest_interval = int(os.environ.get("CS_OPS_QUICKCEP_REST_INTERVAL_SEC", "60"))
    rearm_interval = _REARM_INTERVAL_SEC
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, run_sio_loop)
    # Run rearm scan once at startup, then on its own interval.
    rearm_counter = 0
    try:
        while True:
            try:
                await loop.run_in_executor(None, run_rest_reconcile_once)
            except Exception as exc:
                log.warning("REST reconcile error: %s", exc)
            rearm_counter += rest_interval
            if rearm_counter >= rearm_interval:
                rearm_counter = 0
                try:
                    await loop.run_in_executor(None, run_rearm_scan_once)
                except Exception as exc:
                    log.warning("rearm scan error: %s", exc)
            await asyncio.sleep(rest_interval)
    finally:
        request_stop()
