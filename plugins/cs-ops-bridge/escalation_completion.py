"""Finalize resuming escalations after agent handoff and notify Feishu."""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import cal
from . import feishu_notify

log = logging.getLogger(__name__)

_COMPLETION_PHASES = frozenset({"draft_ready", "failed", "skipped"})


def complete_resuming_escalation_by_id(
    *,
    escalation_id: int,
    phase: str,
    quickcep_session_id: str = "",
    operator_hint: str = "",
    feishu_chat_id: Optional[str] = None,
) -> dict[str, Any]:
    """Post Feishu DONE and finalize one resuming escalation row."""
    if phase not in _COMPLETION_PHASES:
        return {"ok": False, "error": f"invalid phase {phase}"}
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        return {"ok": False, "error": "escalation not found"}
    if str(esc.get("state")) != "resuming":
        return {"ok": False, "error": f"escalation state is {esc.get('state')}, expected resuming"}

    eid = escalation_id
    ctx = esc.get("resume_context") or {}
    qsid = quickcep_session_id or str((esc.get("session") or {}).get("quickcep_session_id") or "")
    chat_id = feishu_chat_id or esc.get("feishu_chat_id")

    if ctx.get("feishu_done_notified"):
        if cal.finalize_escalation(escalation_id=eid, decision=phase):
            return {"ok": True, "escalation_id": eid, "skipped": True, "reason": "already_notified"}
        return {"ok": False, "escalation_id": eid, "error": "finalize failed after prior notify"}

    send = feishu_notify.notify_escalation_completed(
        escalation_id=eid,
        quickcep_session_id=qsid,
        outcome=phase,
        operator_hint=operator_hint,
        feishu_chat_id=chat_id,
        is_retry=bool(ctx.get("retried_at")),
    )
    if not send.ok:
        log.error("feishu escalation done notify failed esc=%s err=%s", eid, send.error)
        return {"ok": False, "escalation_id": eid, "error": send.error}

    cal.merge_escalation_resume_context(
        escalation_id=eid,
        patch={"feishu_done_notified": True, "feishu_done_message_id": send.message_id},
    )
    if not cal.finalize_escalation(escalation_id=eid, decision=phase):
        log.error("finalize escalation failed esc=%s", eid)
        return {"ok": False, "escalation_id": eid, "error": "finalize failed"}

    log.info("escalation completed esc=%s phase=%s session=%s", eid, phase, qsid)
    return {
        "ok": True,
        "escalation_id": eid,
        "feishu_message_id": send.message_id,
        "outcome": phase,
    }


def complete_resuming_escalation_superseded_by_operator(
    *,
    escalation_id: int,
    quickcep_session_id: str = "",
    operator_hint: str = "",
    feishu_chat_id: Optional[str] = None,
) -> dict[str, Any]:
    """Close resuming escalation when operator sent email directly in QuickCEP."""
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        return {"ok": False, "error": "escalation not found"}
    if str(esc.get("state")) != "resuming":
        return {"ok": False, "error": f"escalation state is {esc.get('state')}, expected resuming"}

    eid = escalation_id
    ctx = esc.get("resume_context") or {}
    qsid = quickcep_session_id or str((esc.get("session") or {}).get("quickcep_session_id") or "")
    chat_id = feishu_chat_id or esc.get("feishu_chat_id")
    hint = operator_hint or "客服已在 QuickCEP 直接回复客户，升级关闭"

    run_id = str(ctx.get("resume_run_id") or "").strip()
    run_stopped = False
    if run_id:
        try:
            from .gateway_client import GatewayClient

            run_stopped = bool(GatewayClient.from_env().stop_run(run_id))
            cal.merge_escalation_resume_context(
                escalation_id=eid,
                patch={"resume_run_stopped": run_id, "resume_run_stop_ok": run_stopped},
            )
        except Exception as exc:
            log.warning("stop resume run failed esc=%s run=%s: %s", eid, run_id, exc)

    if ctx.get("feishu_done_notified"):
        if cal.finalize_escalation(escalation_id=eid, decision="operator_manual_reply"):
            return {
                "ok": True,
                "escalation_id": eid,
                "skipped": True,
                "reason": "already_notified",
                "resume_run_stopped": run_stopped,
            }
        return {"ok": False, "escalation_id": eid, "error": "finalize failed after prior notify"}

    send = feishu_notify.notify_escalation_completed(
        escalation_id=eid,
        quickcep_session_id=qsid,
        outcome="operator_manual_reply",
        operator_hint=hint,
        feishu_chat_id=chat_id,
        is_retry=bool(ctx.get("retried_at")),
    )
    if not send.ok:
        log.error("feishu operator-supersede notify failed esc=%s err=%s", eid, send.error)
        return {"ok": False, "escalation_id": eid, "error": send.error}

    cal.merge_escalation_resume_context(
        escalation_id=eid,
        patch={
            "feishu_done_notified": True,
            "feishu_done_message_id": send.message_id,
            "superseded_by_operator_send": True,
        },
    )
    if not cal.finalize_escalation(escalation_id=eid, decision="operator_manual_reply"):
        log.error("finalize escalation failed esc=%s", eid)
        return {"ok": False, "escalation_id": eid, "error": "finalize failed"}

    log.info("escalation superseded by operator send esc=%s session=%s", eid, qsid)
    return {
        "ok": True,
        "escalation_id": eid,
        "feishu_message_id": send.message_id,
        "outcome": "operator_manual_reply",
        "resume_run_stopped": run_stopped,
    }


def complete_resuming_escalation_after_handoff(
    *,
    quickcep_session_id: str,
    phase: str,
    env: str = "LIVE",
    operator_hint: str = "",
) -> Optional[dict[str, Any]]:
    """When resume agent finishes handoff, post Feishu done notice and close escalation."""
    if phase not in _COMPLETION_PHASES:
        return None
    esc = cal.get_resuming_escalation_for_session(quickcep_session_id=quickcep_session_id, env=env)
    if not esc:
        return None
    return complete_resuming_escalation_by_id(
        escalation_id=int(esc["id"]),
        phase=phase,
        quickcep_session_id=quickcep_session_id,
        operator_hint=operator_hint,
        feishu_chat_id=esc.get("feishu_chat_id"),
    )
