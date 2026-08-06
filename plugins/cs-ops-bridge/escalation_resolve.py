"""Operational escalation resolve — align PATCH with DONE notify and session lifecycle."""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import cal
from .escalation_completion import complete_resuming_escalation_by_id

log = logging.getLogger(__name__)

_FAILED_DECISIONS = frozenset(
    {"failed", "cancelled", "timeout", "aborted", "manual_cancel", "stale", "error"}
)


def _outcome_from_decision(decision: str) -> str:
    return "failed" if (decision or "").lower() in _FAILED_DECISIONS else "draft_ready"


def resolve_escalation_operational(
    *,
    escalation_id: int,
    decision: str,
    decided_by: str,
    operator_answer: Optional[str] = None,
    final_state: str = "resolved",
    operator_hint: str = "",
) -> dict[str, Any]:
    """Resolve an escalation with Feishu DONE for resuming rows; avoid session regressions."""
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        log.info("cs.escalation.resolve_op escalation_id=%s decision=failed reason=not_found", escalation_id)
        return {"ok": False, "error": "escalation not found"}

    state = str(esc.get("state") or "")

    if final_state == "resolved" and state == "resuming":
        qsid = str((esc.get("session") or {}).get("quickcep_session_id") or "")
        phase = _outcome_from_decision(decision)
        hint = operator_hint or f"manual resolve ({decision})"
        log.info(
            "cs.escalation.resolve_op escalation_id=%s session=%s prior_state=resuming "
            "path=completion phase=%s decision=%s decided_by=%s",
            escalation_id, qsid, phase, decision, decided_by,
        )
        completion = complete_resuming_escalation_by_id(
            escalation_id=escalation_id,
            phase=phase,
            quickcep_session_id=qsid,
            operator_hint=hint,
            feishu_chat_id=esc.get("feishu_chat_id"),
        )
        if not completion.get("ok"):
            log.info(
                "cs.escalation.resolve_op escalation_id=%s session=%s path=completion "
                "decision=completion_failed error=%s",
                escalation_id, qsid, completion.get("error"),
            )
            return completion
        if operator_answer or decided_by or decision:
            cal.patch_escalation_decision(
                escalation_id=escalation_id,
                decision=decision,
                decided_by=decided_by,
                operator_answer=operator_answer,
            )
        log.info(
            "cs.escalation.resolve_op escalation_id=%s session=%s path=completion "
            "decision=resolved to_state=resolved", escalation_id, qsid,
        )
        return {"ok": True, "escalation_id": escalation_id, "completion": completion}

    touch_session = state == "awaiting_answer" and final_state == "resolved"
    log.info(
        "cs.escalation.resolve_op escalation_id=%s prior_state=%s path=direct "
        "final_state=%s touch_session=%s decision=%s decided_by=%s",
        escalation_id, state, final_state, touch_session, decision, decided_by,
    )
    ok = cal.resolve_escalation(
        escalation_id=escalation_id,
        decision=decision,
        decided_by=decided_by,
        operator_answer=operator_answer,
        final_state=final_state,
        touch_session=touch_session,
    )
    if not ok:
        log.info(
            "cs.escalation.resolve_op escalation_id=%s path=direct decision=resolve_failed",
            escalation_id,
        )
        return {"ok": False, "error": "resolve failed"}
    note = None
    if state == "awaiting_answer":
        note = "closed without Feishu DONE (no operator reply processed)"
    log.info(
        "cs.escalation.resolve_op escalation_id=%s path=direct decision=resolved "
        "to_state=%s note=%s", escalation_id, final_state, bool(note),
    )
    return {"ok": True, "escalation_id": escalation_id, "note": note}
