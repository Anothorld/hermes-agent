"""Close open escalations when a CS operator sends email directly in QuickCEP."""

from __future__ import annotations

import logging
import os
from typing import Any

from . import cal
from .escalation_completion import complete_resuming_escalation_superseded_by_operator

log = logging.getLogger(__name__)

_OPEN_STATES = ("awaiting_answer", "resuming")
_DECISION = "operator_manual_reply"
_DECIDED_BY = "bridge:operator_send"


def _close_escalations_enabled() -> bool:
    return os.environ.get("CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC", "true").lower() in (
        "1",
        "true",
        "yes",
    )


def _close_one_escalation(
    esc: dict[str, Any],
    *,
    quickcep_session_id: str,
    env: str,
    operator_hint: str,
) -> dict[str, Any]:
    eid = int(esc["id"])
    state = str(esc.get("state") or "")
    if state == "resuming":
        result = complete_resuming_escalation_superseded_by_operator(
            escalation_id=eid,
            quickcep_session_id=quickcep_session_id,
            operator_hint=operator_hint,
            feishu_chat_id=esc.get("feishu_chat_id"),
        )
    else:
        ok = cal.resolve_escalation(
            escalation_id=eid,
            decision=_DECISION,
            decided_by=_DECIDED_BY,
            operator_answer=operator_hint,
            final_state="resolved",
            touch_session=False,
        )
        result = {"ok": ok, "escalation_id": eid}
        if ok:
            cal.merge_escalation_resume_context(
                escalation_id=eid,
                patch={"superseded_by_operator_send": True},
            )

    if result.get("ok"):
        cal.write_event(
            quickcep_session_id=quickcep_session_id,
            event_type="escalation_superseded_by_operator_send",
            payload={"escalation_id": eid, "prior_state": state, "decision": _DECISION},
            env=env,
        )
        log.info(
            "escalation closed on operator send esc=%s state=%s session=%s",
            eid,
            state,
            quickcep_session_id,
        )
        log.info(
            "cs.escalation.close escalation_id=%s session=%s env=%s prior_state=%s "
            "decision=closed reason=operator_manual_reply to_state=resolved",
            eid, quickcep_session_id, env, state,
        )
    else:
        log.warning(
            "escalation close failed on operator send esc=%s state=%s session=%s err=%s",
            eid,
            state,
            quickcep_session_id,
            result.get("error"),
        )
        log.info(
            "cs.escalation.close escalation_id=%s session=%s env=%s prior_state=%s "
            "decision=close_failed error=%s",
            eid, quickcep_session_id, env, state, result.get("error"),
        )
    return {"escalation_id": eid, "prior_state": state, **result}


def close_escalations_on_operator_manual_reply(
    *,
    quickcep_session_id: str,
    env: str = "LIVE",
    operator_hint: str = "",
) -> dict[str, Any]:
    """Resolve awaiting_answer / resuming escalations after manual operator outbound."""
    if not _close_escalations_enabled():
        return {"ok": True, "skipped": True, "reason": "disabled", "closed": []}

    hint = operator_hint or "客服已在 QuickCEP 直接回复客户，升级关闭"
    closed: list[dict[str, Any]] = []

    for esc in cal.list_escalations_for_session(
        quickcep_session_id=quickcep_session_id,
        states=_OPEN_STATES,
        env=env,
    ):
        item = _close_one_escalation(
            esc,
            quickcep_session_id=quickcep_session_id,
            env=env,
            operator_hint=hint,
        )
        if item.get("ok"):
            closed.append(item)

    return {"ok": True, "closed": closed}


def repair_orphaned_escalations_once(*, env: str | None = None) -> dict[str, Any]:
    """Close open escalations superseded by a *current-cycle* operator send.

    Only an ``operator_sent`` event at or after the escalation's ``created_at``
    counts. A prior-cycle send (customer reopen → new escalation) must not
    auto-close the new ticket — that was the coletteallen@verizon.net false
    ``superseded_by_operator_send`` bug.
    """
    env = env or os.environ.get("CS_OPS_ENV", "LIVE")
    if not _close_escalations_enabled():
        return {"ok": True, "skipped": True, "reason": "disabled", "checked": 0, "repaired": 0}

    checked = 0
    repaired = 0
    seen_row_ids: set[int] = set()

    for sess in cal.list_sessions_with_open_escalations(env=env, limit=200):
        row_id = int(sess["id"])
        if row_id in seen_row_ids:
            continue
        seen_row_ids.add(row_id)
        checked += 1

        sid = str(sess.get("quickcep_session_id") or "")
        if not sid:
            continue

        closed_ids: list[int] = []
        for esc in cal.list_escalations_for_session(
            quickcep_session_id=sid,
            states=_OPEN_STATES,
            env=env,
        ):
            esc_created = str(esc.get("created_at") or "")
            if not esc_created:
                continue
            if not cal.session_has_event_since(
                session_row_id=row_id,
                event_type="operator_sent",
                since=esc_created,
            ):
                continue
            item = _close_one_escalation(
                esc,
                quickcep_session_id=sid,
                env=env,
                operator_hint="repair: 会话已标记为客服已回复，补关升级",
            )
            if item.get("ok"):
                closed_ids.append(int(item["escalation_id"]))

        if closed_ids:
            repaired += 1
            log.info(
                "orphaned escalation repair session=%s closed=%s",
                sid,
                closed_ids,
            )
            log.info(
                "cs.escalation.close session=%s env=%s decision=repair_closed "
                "escalation_ids=%s reason=orphaned_after_operator_send",
                sid, env, closed_ids,
            )

    return {"ok": True, "checked": checked, "repaired": repaired}
