"""Shared escalation resume — gateway launch then CAL resolve."""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import cal
from .gateway_client import GatewayClient

log = logging.getLogger(__name__)


def resume_escalation(
    *,
    escalation_id: int,
    operator_answer: str,
    decided_by: str,
    env: str = "LIVE",
    feishu_reply_message_id: str | None = None,
    already_claimed: bool = False,
    feishu_messages: Optional[list[dict[str, Any]]] = None,
    feishu_token: Optional[str] = None,
    exclude_feishu_message_ids: Optional[set[str]] = None,
    feishu_after_ms: int = 0,
    skip_attachment_prepare: bool = False,
) -> dict[str, Any]:
    """Launch gateway resume run for a claimed escalation (stays resuming until handoff completes)."""
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        log.info("cs.escalation.resume escalation_id=%s decision=failed reason=not_found", escalation_id)
        return {"ok": False, "error": "escalation not found"}

    state = str(esc.get("state"))
    if state == "awaiting_answer" and not already_claimed:
        reply_mid = feishu_reply_message_id or f"manual:{escalation_id}"
        if not cal.claim_escalation_reply(
            escalation_id=escalation_id,
            operator_answer=operator_answer,
            decided_by=decided_by,
            feishu_reply_message_id=reply_mid,
        ):
            log.info(
                "cs.escalation.resume escalation_id=%s decision=failed "
                "reason=claim_lost_or_not_awaiting decided_by=%s",
                escalation_id, decided_by,
            )
            return {"ok": False, "error": "escalation already claimed or not awaiting_answer"}
        esc = cal.get_escalation(escalation_id=escalation_id) or esc
        state = str(esc.get("state"))

    if state != "resuming":
        log.info(
            "cs.escalation.resume escalation_id=%s decision=failed "
            "reason=state_not_resuming state=%s", escalation_id, state,
        )
        return {"ok": False, "error": f"escalation state is {state}, expected resuming"}

    ctx = esc.get("resume_context") or {}
    if ctx.get("resume_run_id"):
        log.info(
            "cs.escalation.resume escalation_id=%s decision=deduped "
            "run_id=%s reason=resume_run_id_already_set",
            escalation_id, ctx["resume_run_id"],
        )
        return {
            "ok": True,
            "run_id": ctx["resume_run_id"],
            "escalation_id": escalation_id,
            "dedup_skipped": True,
        }

    if not skip_attachment_prepare:
        from .escalation_attachments import prepare_escalation_attachments

        prepare_escalation_attachments(
            escalation_id=escalation_id,
            feishu_messages=feishu_messages,
            feishu_token=feishu_token,
            exclude_message_ids=exclude_feishu_message_ids,
            after_ms=feishu_after_ms,
        )
        esc = cal.get_escalation(escalation_id=escalation_id) or esc
        ctx = esc.get("resume_context") or {}

    sess = esc.get("session") or {}
    qsid = str(sess.get("quickcep_session_id") or "")
    if not qsid:
        log.info(
            "cs.escalation.resume escalation_id=%s decision=failed reason=missing_quickcep_session",
            escalation_id,
        )
        return {"ok": False, "error": "missing quickcep session on escalation"}

    ctx = esc.get("resume_context") or {}
    answer = (
        str(ctx.get("operator_answer_raw") or "")
        or (esc.get("operator_answer") or operator_answer or "")
    ).strip()
    if not answer:
        log.info(
            "cs.escalation.resume escalation_id=%s session=%s decision=failed "
            "reason=operator_answer_required", escalation_id, qsid,
        )
        return {"ok": False, "error": "operator_answer required"}

    operator_attachments = ctx.get("operator_attachments") or []
    allowed_urls = ctx.get("allowed_attachment_urls") or []

    outcome = GatewayClient.from_env().start_resume_run(
        escalation_id=escalation_id,
        quickcep_session_id=qsid,
        env=env,
        operator_answer=answer,
        operator_attachments=operator_attachments,
        allowed_attachment_urls=allowed_urls,
    )
    if not outcome.run_id:
        err = "launch deduped" if outcome.dedup_skipped else "gateway launch failed"
        log.error("resume escalation %s: %s", escalation_id, err)
        log.info(
            "cs.escalation.resume escalation_id=%s session=%s decision=failed "
            "reason=%s dedup_skipped=%s", escalation_id, qsid, err, outcome.dedup_skipped,
        )
        return {"ok": False, "error": err, "dedup_skipped": outcome.dedup_skipped}

    cal.record_escalation_resume_run(escalation_id=escalation_id, run_id=str(outcome.run_id))
    log.info(
        "cs.escalation.resume escalation_id=%s session=%s env=%s decision=launched "
        "run_id=%s decided_by=%s",
        escalation_id, qsid, env, outcome.run_id, decided_by,
    )
    return {"ok": True, "run_id": outcome.run_id, "escalation_id": escalation_id}


def console_reply_escalation(
    *,
    escalation_id: int,
    operator_answer: str,
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    env: str = "LIVE",
) -> dict[str, Any]:
    """Console-originated escalation reply (PR1.8).

    Shares the atomic first-wins claim with the Feishu path
    (``cal.claim_escalation_reply`` → awaiting_answer → resuming), then posts
    the ``[ESC-LOCK:…]`` notice to the Feishu thread (so both channels see the
    lock), launches the gateway resume run, and records an audit event.

    Returns ``{ok, claimed, run_id, escalation_id}`` on success; on a lost
    first-wins race returns ``{ok: False, error: "already_claimed"}``.
    """
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        return {"ok": False, "error": "escalation not found"}
    decided_by = f"console:{operator_id}" if operator_id else (operator_name or "console_operator")
    reply_mid = f"console:{operator_id or 'manual'}:{escalation_id}"

    claimed = cal.claim_escalation_reply(
        escalation_id=escalation_id,
        operator_answer=operator_answer,
        decided_by=decided_by,
        feishu_reply_message_id=reply_mid,
    )
    if not claimed:
        return {"ok": False, "error": "already_claimed",
                "error_detail": "first reply wins — escalation already claimed"}

    # Best-effort Feishu [ESC-LOCK] notice so the Feishu side stops accepting replies.
    # Idempotent: skip if already notified (e.g. Feishu path claimed first), and
    # persist feishu_lock_notified + message_id so the poller doesn't re-post.
    feishu_root = esc.get("feishu_message_id")
    esc_ctx = esc.get("resume_context") or {}
    if feishu_root and not esc_ctx.get("feishu_lock_notified"):
        try:
            from .feishu_notify import notify_escalation_locked

            lock = notify_escalation_locked(escalation_id=escalation_id, feishu_root_message_id=feishu_root)
            if lock.ok:
                cal.merge_escalation_resume_context(
                    escalation_id=escalation_id,
                    patch={"feishu_lock_notified": True, "feishu_lock_message_id": lock.message_id},
                )
            else:
                log.warning("console ESC-LOCK notify failed esc=%s: %s", escalation_id, lock.error)
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.warning("console ESC-LOCK notify failed esc=%s: %s", escalation_id, exc)

    # Record audit event on the session.
    sess = esc.get("session") or {}
    qsid = str(sess.get("quickcep_session_id") or "")
    if qsid:
        cal.write_event(
            quickcep_session_id=qsid,
            env=env,
            event_type="escalation_reply_console",
            payload={
                "escalation_id": escalation_id,
                "operator_id": operator_id,
                "operator_name": operator_name,
                "decided_by": decided_by,
            },
        )

    # Launch the resume run (already claimed → skips re-claim).
    resume = resume_escalation(
        escalation_id=escalation_id,
        operator_answer=operator_answer,
        decided_by=decided_by,
        env=env,
        already_claimed=True,
        skip_attachment_prepare=False,
    )
    return {
        "ok": bool(resume.get("ok")),
        "claimed": True,
        "run_id": resume.get("run_id"),
        "escalation_id": escalation_id,
        "resume": resume,
    }


# ---------------------------------------------------------------------------
# Resume failure detection + manual retry (档位 B)
# ---------------------------------------------------------------------------

_RESUME_RETRY_ALLOWED_STATUSES = frozenset(
    {"processing", "awaiting_expert", "draft_ready", "failed"}
)

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "stopped"})


def _parse_session_id(session_id: str) -> tuple[str, str] | None:
    """Parse ``povison-cs:{env}:{qsid}`` → (env, qsid). Returns None if format mismatches."""
    parts = (session_id or "").split(":", 2)
    if len(parts) != 3:
        return None
    _profile, env, qsid = parts
    if not env or not qsid:
        return None
    return env, qsid


def handle_resume_run_finished(
    *,
    session_id: str,
    completed: bool = True,
    env: str = "LIVE",
) -> dict[str, Any]:
    """Detect resume runs that ended without applying handoff and notify operators.

    Called by the bridge ``POST /internal/run-finished`` endpoint (triggered
    by the gateway ``on_session_end`` hook). If the escalation is still
    ``resuming`` after the run ended, the agent never called ``apply-handoff``
    — the ESC36/37 failure mode. Writes a CAL event + Feishu notification so
    the operator can manually retry via the Console「重新生成」button.
    """
    parsed = _parse_session_id(session_id)
    if not parsed:
        return {"ok": True, "action": "noop", "reason": "unparseable session_id"}
    parsed_env, qsid = parsed

    esc = cal.get_resuming_escalation_for_session(quickcep_session_id=qsid, env=parsed_env)
    if not esc:
        return {"ok": True, "action": "noop", "reason": "no resuming escalation"}

    eid = int(esc["id"])
    ctx = esc.get("resume_context") or {}

    # Idempotent: don't notify twice for the same failure.
    if ctx.get("resume_failed_notified"):
        return {"ok": True, "action": "noop", "reason": "already notified"}

    # False-positive guard: if the resume run is still running, this callback
    # came from a different run (e.g. operator_edit_memory) — skip.
    resume_run_id = str(ctx.get("resume_run_id") or "")
    if resume_run_id:
        try:
            run_status = GatewayClient.from_env().get_run_status(resume_run_id)
        except Exception as exc:
            log.warning("handle_resume_run_finished: get_run_status failed esc=%s: %s", eid, exc)
            run_status = None
        if run_status and str(run_status.get("status", "")) not in _TERMINAL_RUN_STATUSES:
            return {"ok": True, "action": "noop", "reason": "resume run still running"}

    # --- Detection confirmed: run ended but escalation still resuming ---
    reason = "run ended without handoff"
    is_retry = bool(ctx.get("retried_at"))
    cal.merge_escalation_resume_context(
        escalation_id=eid,
        patch={
            "resume_failed_detected": True,
            "resume_fail_reason": reason,
            "resume_failed_notified": True,
        },
    )
    cal.write_event(
        quickcep_session_id=qsid,
        env=parsed_env,
        event_type="escalation_resume_failed",
        payload={
            "escalation_id": eid,
            "reason": reason,
            "run_id": resume_run_id,
            "is_retry": is_retry,
        },
    )

    # Feishu notification (skip if no Feishu thread — console-only escalation).
    feishu_msg_id = str(esc.get("feishu_message_id") or "")
    try:
        from . import feishu_notify

        feishu_notify.notify_escalation_resume_failed(
            escalation_id=eid,
            quickcep_session_id=qsid,
            feishu_message_id=feishu_msg_id,
            reason=reason,
            is_retry=is_retry,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("resume failed Feishu notify failed esc=%s: %s", eid, exc)

    log.warning(
        "resume failure detected esc=%s session=%s run=%s is_retry=%s",
        eid, qsid, resume_run_id, is_retry,
    )
    log.info(
        "cs.escalation.resume_failed escalation_id=%s session=%s env=%s run_id=%s "
        "is_retry=%s decision=notified reason=%s",
        eid, qsid, parsed_env, resume_run_id, is_retry, reason,
    )
    return {"ok": True, "action": "notified", "escalation_id": eid}


def retry_resume_for_session(*, quickcep_session_id: str, env: str) -> dict[str, Any]:
    """Manually retry a failed resume from the operator's「重新生成」button.

    Reuses the existing ``resume_escalation`` launch path after reopening the
    escalation (clears ``resume_run_id``, resets the 4h timeout anchor, and
    clears failure markers). Only triggers when the session has an escalation
    with a recorded expert answer; otherwise returns ``no_resume`` so the
    caller falls through to the normal inbound relaunch path.
    """
    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return {"ok": False, "kind": "no_resume"}
    # Guard: don't redo resume when the operator already replied to the customer.
    if str(sess.get("status")) not in _RESUME_RETRY_ALLOWED_STATUSES:
        return {"ok": False, "kind": "no_resume"}

    esc = cal.get_latest_escalation_with_operator_answer(
        quickcep_session_id=quickcep_session_id, env=env,
    )
    if not esc:
        return {"ok": False, "kind": "no_resume"}

    eid = int(esc["id"])
    ctx = esc.get("resume_context") or {}
    old_run_id = str(ctx.get("resume_run_id") or "")

    # 1. Best-effort stop the old run (it may have already ended — stop returns False).
    if old_run_id:
        try:
            GatewayClient.from_env().stop_run(old_run_id)
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.warning("stop old resume run failed esc=%s run=%s: %s", eid, old_run_id, exc)

    # 2. Atomically reopen: any state → resuming, clear run_id + failure markers,
    #    reset resume_launched_at=now (4h timeout anchor), set retried_at.
    cal.reopen_escalation_for_resume(escalation_id=eid)

    # 3. Do NOT change session status — let the resume agent's apply-handoff
    #    drive the lifecycle naturally (avoids rank regression / stale interaction).

    # 4. Relaunch the resume run (already claimed). Re-prepare vault→CDN when
    # operator_attachments were never merged (e.g. console reply bug on first launch).
    answer = str(ctx.get("operator_answer_raw") or "").strip()
    result = resume_escalation(
        escalation_id=eid,
        operator_answer=answer,
        decided_by=str(esc.get("decided_by") or "console_retry"),
        env=env,
        already_claimed=True,
        skip_attachment_prepare=bool(ctx.get("operator_attachments")),
    )
    result["kind"] = "resume_retry"
    result["escalation_id"] = eid
    log.info(
        "cs.escalation.resume_retry escalation_id=%s session=%s env=%s old_run_id=%s "
        "new_run_id=%s decision=%s",
        eid, quickcep_session_id, env, old_run_id, result.get("run_id"),
        "relaunched" if result.get("ok") else "no_resume",
    )
    return result
