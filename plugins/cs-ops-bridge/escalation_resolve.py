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
        return {"ok": False, "error": "escalation not found"}

    state = str(esc.get("state") or "")

    if final_state == "resolved" and state == "resuming":
        qsid = str((esc.get("session") or {}).get("quickcep_session_id") or "")
        phase = _outcome_from_decision(decision)
        hint = operator_hint or f"manual resolve ({decision})"
        completion = complete_resuming_escalation_by_id(
            escalation_id=escalation_id,
            phase=phase,
            quickcep_session_id=qsid,
            operator_hint=hint,
            feishu_chat_id=esc.get("feishu_chat_id"),
        )
        if not completion.get("ok"):
            return completion
        if operator_answer or decided_by or decision:
            cal.patch_escalation_decision(
                escalation_id=escalation_id,
                decision=decision,
                decided_by=decided_by,
                operator_answer=operator_answer,
            )
        return {"ok": True, "escalation_id": escalation_id, "completion": completion}

    touch_session = state == "awaiting_answer" and final_state == "resolved"
    ok = cal.resolve_escalation(
        escalation_id=escalation_id,
        decision=decision,
        decided_by=decided_by,
        operator_answer=operator_answer,
        final_state=final_state,
        touch_session=touch_session,
    )
    if not ok:
        return {"ok": False, "error": "resolve failed"}
    note = None
    if state == "awaiting_answer":
        note = "closed without Feishu DONE (no operator reply processed)"
    return {"ok": True, "escalation_id": escalation_id, "note": note}
