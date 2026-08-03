"""Recover sessions stuck in ``processing`` after agent runs die without handoff."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import cal
from .session_handoff import apply_handoff

log = logging.getLogger(__name__)

_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
# Default 2h — only applies to ``processing``. ``awaiting_expert`` is excluded entirely
# (Feishu escalation poller + escalation SLA handle that lifecycle).
_STALE_MIN = float(os.environ.get("CS_OPS_PROCESSING_STALE_MIN", "120"))
# Short-heartbeat threshold: if ``agent_processing_at`` (the timestamp the agent
# stamps when it first confirms processing) is older than this AND ``updated_at``
# is also older than this, the session is treated as stale even before the 2h
# hard cap. This catches crashed/killed agent runs within ~15 min instead of
# waiting 2h. Set to 0 to disable the heartbeat path and rely only on _STALE_MIN.
_HEARTBEAT_MIN = float(os.environ.get("CS_OPS_PROCESSING_HEARTBEAT_MIN", "15"))
_STALE_TARGET_STATUS = "processing"


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_since(ts: str, *, now: datetime) -> Optional[float]:
    parsed = _parse_ts(ts)
    if not parsed:
        return None
    return (now - parsed.astimezone(timezone.utc)).total_seconds() / 60.0


def _stale_threshold_label() -> str:
    """Operator-facing duration for failed handoff notes."""
    if _STALE_MIN >= 60:
        hours = _STALE_MIN / 60.0
        return f"{hours:g} 小时" if hours == int(hours) else f"{hours:.1f} 小时"
    return f"{_STALE_MIN:g} 分钟"


def check_processing_stale_once() -> dict[str, Any]:
    """Mark orphaned ``processing`` sessions as ``failed`` so inbound can relaunch.

    Only ``processing`` rows are scanned. Sessions in ``awaiting_expert`` wait for
    Feishu operator replies without this timeout.
    """
    now = datetime.now(timezone.utc)
    state = cal.get_poller_state("processing_stale")
    recovered_ids: list[str] = list(state.get("recovered_ids") or [])[-100:]
    newly_recovered = 0

    for sess in cal.list_sessions(env=_ENV, status=_STALE_TARGET_STATUS, limit=200):
        qsid = str(sess.get("quickcep_session_id") or "")
        if not qsid:
            continue
        elapsed_min = _minutes_since(str(sess.get("updated_at") or ""), now=now)
        if elapsed_min is None:
            continue

        # Two recovery paths:
        #   1. Hard cap: updated_at older than _STALE_MIN (2h) — always recover.
        #   2. Heartbeat: agent_processing_at AND updated_at both older than
        #      _HEARTBEAT_MIN (15min) — the agent likely crashed/killed and
        #      never stamped a recent activity. Recovers fast instead of waiting 2h.
        #      agent_processing_at may be NULL (v5 session / agent never confirmed);
        #      in that case fall back to the hard cap only.
        is_stale = elapsed_min >= _STALE_MIN
        trigger = "hard_cap"
        if not is_stale and _HEARTBEAT_MIN > 0:
            agent_ts = _minutes_since(str(sess.get("agent_processing_at") or ""), now=now)
            if agent_ts is not None and agent_ts >= _HEARTBEAT_MIN and elapsed_min >= _HEARTBEAT_MIN:
                is_stale = True
                trigger = "heartbeat"

        if not is_stale:
            continue

        result = apply_handoff(
            quickcep_session_id=qsid,
            phase="failed",
            env=_ENV,
            context={
                "error": f"自动处理超过 {_stale_threshold_label()}未完成，已释放会话供重新处理或人工接手",
                "customer_need": "客户来信仍在等待回复",
                "actions_taken": "系统检测到处理线程中断，已自动结束本轮处理",
                "follow_up": "可在 QuickCEP 人工回复，或等待客户新消息后系统自动重试",
                "operator_hint": "如已有人工草稿可直接发送；否则请产品同事协助后回复客户",
            },
        )
        if result.get("ok") and not result.get("skipped"):
            newly_recovered += 1
            if qsid not in recovered_ids:
                recovered_ids.append(qsid)
            log.warning(
                "processing stale recovered session=%s elapsed_min=%.1f trigger=%s",
                qsid,
                elapsed_min,
                trigger,
            )
            try:
                cal.write_event(
                    quickcep_session_id=qsid,
                    env=_ENV,
                    event_type="processing_stale_recovered",
                    payload={
                        "elapsed_min": elapsed_min,
                        "threshold_min": _STALE_MIN,
                        "trigger": trigger,
                        "heartbeat_min": _HEARTBEAT_MIN if trigger == "heartbeat" else None,
                        "agent_processing_at": sess.get("agent_processing_at"),
                    },
                )
            except Exception as exc:
                log.warning("processing_stale_recovered event write failed session=%s: %s", qsid, exc)
        else:
            log.warning(
                "processing stale handoff skipped/failed session=%s: %s",
                qsid,
                result.get("reason") or result.get("error"),
            )

    cal.set_poller_state(
        "processing_stale",
        {
            "recovered_ids": recovered_ids,
            "last_run": time.time(),
            "newly_recovered": newly_recovered,
        },
    )
    return {"newly_recovered": newly_recovered, "tracked": len(recovered_ids)}


async def start_background() -> None:
    interval = int(os.environ.get("CS_OPS_PROCESSING_STALE_INTERVAL_SEC", "120"))
    while True:
        try:
            check_processing_stale_once()
        except Exception as exc:
            log.warning("processing stale check error: %s", exc)
        await asyncio.sleep(interval)
