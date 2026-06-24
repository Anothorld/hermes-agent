"""Escalation SLA timeout tracking, resuming stale cleanup, and Feishu reminders."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import cal
from .escalation_completion import complete_resuming_escalation_by_id
from .feishu_client import reply_to_message, tenant_access_token

log = logging.getLogger(__name__)

_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
_TIMEOUT_HOURS = {
    "high": float(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_HIGH_H", "2")),
    "medium": float(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_MED_H", "8")),
    "low": float(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_LOW_H", "24")),
}
_RESUMING_TIMEOUT_H = float(os.environ.get("CS_OPS_ESCALATION_RESUMING_TIMEOUT_H", "4"))


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_since(ts: str, *, now: datetime) -> Optional[float]:
    parsed = _parse_ts(ts)
    if not parsed:
        return None
    return (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600


def check_resuming_stale_once() -> dict[str, Any]:
    """Finalize resuming escalations when resume run never completes handoff."""
    now = datetime.now(timezone.utc)
    state = cal.get_poller_state("escalation_resuming_timeout")
    handled: set[str] = set(state.get("handled_ids") or [])
    newly_handled = 0

    for esc in cal.list_escalations(state="resuming", env=_ENV):
        eid = str(esc["id"])
        if eid in handled:
            continue
        ctx = esc.get("resume_context") or {}
        if ctx.get("resuming_timeout_handled"):
            handled.add(eid)
            continue
        anchor = str(ctx.get("resume_launched_at") or ctx.get("claimed_at") or esc.get("decided_at") or "")
        elapsed_h = _hours_since(anchor, now=now)
        if elapsed_h is None or elapsed_h < _RESUMING_TIMEOUT_H:
            continue
        qsid = str(esc.get("quickcep_session_id") or "")
        result = complete_resuming_escalation_by_id(
            escalation_id=int(eid),
            phase="failed",
            quickcep_session_id=qsid,
            operator_hint=f"resume 超过 {_RESUMING_TIMEOUT_H:g}h 未完成，已自动关闭",
            feishu_chat_id=esc.get("feishu_chat_id"),
        )
        if result.get("ok"):
            cal.merge_escalation_resume_context(
                escalation_id=int(eid),
                patch={"resuming_timeout_handled": True},
            )
            handled.add(eid)
            newly_handled += 1
            log.warning("resuming escalation timed out esc=%s elapsed_h=%.1f", eid, elapsed_h)
        else:
            log.warning("resuming timeout finalize failed esc=%s: %s", eid, result.get("error"))

    cal.set_poller_state(
        "escalation_resuming_timeout",
        {"handled_ids": sorted(handled), "last_run": time.time(), "newly_handled": newly_handled},
    )
    return {"newly_handled": newly_handled, "tracked": len(handled)}


def check_timeouts_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = cal.get_poller_state("escalation_timeout")
    reminded: set[str] = set(state.get("reminded_ids") or [])
    newly_reminded = 0
    token = tenant_access_token()

    for esc in cal.list_escalations(state="awaiting_answer", env=_ENV):
        eid = str(esc["id"])
        if eid in reminded:
            continue
        created = _parse_ts(str(esc.get("created_at") or ""))
        if not created:
            continue
        hours = _TIMEOUT_HOURS.get(str(esc.get("urgency") or "medium").lower(), 8)
        elapsed_h = (now - created.astimezone(timezone.utc)).total_seconds() / 3600
        if elapsed_h < hours:
            continue
        qsid = ""
        full = cal.get_escalation(escalation_id=int(eid))
        if full and full.get("session"):
            qsid = str(full["session"].get("quickcep_session_id") or "")
        if qsid:
            cal.write_event(
                quickcep_session_id=qsid,
                event_type="escalation_timeout",
                payload={"escalation_id": int(eid), "urgency": esc.get("urgency"), "elapsed_hours": round(elapsed_h, 1)},
                env=_ENV,
            )
        msg_id = esc.get("feishu_message_id")
        if token and msg_id:
            text = (
                f"⏰ [ESC:{eid}] 已超过 {hours:g}h 未收到后援回复，请尽快 @AI客服 处理。"
            )
            send = reply_to_message(token=str(token), message_id=str(msg_id), text=text)
            if send.ok:
                reminded.add(eid)
                newly_reminded += 1
        else:
            reminded.add(eid)
            newly_reminded += 1
            log.warning("escalation %s timed out (no feishu message_id for reminder)", eid)

    cal.set_poller_state(
        "escalation_timeout",
        {"reminded_ids": sorted(reminded), "last_run": time.time(), "newly_reminded": newly_reminded},
    )
    resuming_stats = check_resuming_stale_once()
    return {
        "newly_reminded": newly_reminded,
        "tracked": len(reminded),
        **resuming_stats,
    }


async def start_background() -> None:
    import asyncio

    interval = int(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_INTERVAL_SEC", "900"))
    while True:
        try:
            check_timeouts_once()
        except Exception as exc:
            log.warning("escalation timeout check error: %s", exc)
        await asyncio.sleep(interval)
