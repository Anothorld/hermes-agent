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
        if elapsed_min is None or elapsed_min < _STALE_MIN:
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
                "processing stale recovered session=%s elapsed_min=%.1f",
                qsid,
                elapsed_min,
            )
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
