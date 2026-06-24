"""Shared escalation resume — gateway launch then CAL resolve."""

from __future__ import annotations

import logging
from typing import Any

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

    if ctx.get("resume_run_id"):
        return {
            "ok": True,
            "run_id": ctx["resume_run_id"],
            "escalation_id": escalation_id,
            "dedup_skipped": True,
        }

    outcome = GatewayClient.from_env().start_resume_run(
        escalation_id=escalation_id,
        quickcep_session_id=qsid,
        env=env,
        operator_answer=answer,
    )
    if not outcome.run_id:
        err = "launch deduped" if outcome.dedup_skipped else "gateway launch failed"
        log.error("resume escalation %s: %s", escalation_id, err)
        return {"ok": False, "error": err, "dedup_skipped": outcome.dedup_skipped}

    cal.record_escalation_resume_run(escalation_id=escalation_id, run_id=str(outcome.run_id))
    return {"ok": True, "run_id": outcome.run_id, "escalation_id": escalation_id}
