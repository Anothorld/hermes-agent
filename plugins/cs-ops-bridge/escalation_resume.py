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
) -> dict[str, Any]:
    """Launch gateway resume run; resolve escalation only after accept."""
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        return {"ok": False, "error": "escalation not found"}
    if str(esc.get("state")) != "awaiting_answer":
        return {"ok": False, "error": f"escalation state is {esc.get('state')}, not awaiting_answer"}
    sess = esc.get("session") or {}
    qsid = str(sess.get("quickcep_session_id") or "")
    if not qsid:
        return {"ok": False, "error": "missing quickcep session on escalation"}
    answer = (operator_answer or "").strip()
    if not answer:
        return {"ok": False, "error": "operator_answer required"}

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

    cal.resolve_escalation(
        escalation_id=escalation_id,
        decision="resume",
        decided_by=decided_by,
        operator_answer=answer,
        final_state="resolved",
    )
    return {"ok": True, "run_id": outcome.run_id, "escalation_id": escalation_id}
