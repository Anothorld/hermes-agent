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
            return {"ok": False, "error": "escalation already claimed or not awaiting_answer"}
        esc = cal.get_escalation(escalation_id=escalation_id) or esc
        state = str(esc.get("state"))

    if state != "resuming":
        return {"ok": False, "error": f"escalation state is {state}, expected resuming"}

    ctx = esc.get("resume_context") or {}
    if ctx.get("resume_run_id"):
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
        return {"ok": False, "error": "missing quickcep session on escalation"}

    ctx = esc.get("resume_context") or {}
    answer = (
        str(ctx.get("operator_answer_raw") or "")
        or (esc.get("operator_answer") or operator_answer or "")
    ).strip()
    if not answer:
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
        return {"ok": False, "error": err, "dedup_skipped": outcome.dedup_skipped}

    cal.record_escalation_resume_run(escalation_id=escalation_id, run_id=str(outcome.run_id))
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
    feishu_root = esc.get("feishu_message_id")
    if feishu_root:
        try:
            from .feishu_notify import notify_escalation_locked

            notify_escalation_locked(escalation_id=escalation_id, feishu_root_message_id=feishu_root)
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
        skip_attachment_prepare=True,
    )
    return {
        "ok": bool(resume.get("ok")),
        "claimed": True,
        "run_id": resume.get("run_id"),
        "escalation_id": escalation_id,
        "resume": resume,
    }
