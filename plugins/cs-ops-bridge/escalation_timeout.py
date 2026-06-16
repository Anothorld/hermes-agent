"""Escalation SLA timeout tracking and Feishu thread reminders."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from . import cal

log = logging.getLogger(__name__)

_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
_TIMEOUT_HOURS = {
    "high": float(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_HIGH_H", "2")),
    "medium": float(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_MED_H", "8")),
    "low": float(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_LOW_H", "24")),
}


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _feishu_token() -> Optional[str]:
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        return None
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data.get("tenant_access_token")


def _post_thread_reply(*, token: str, message_id: str, text: str) -> bool:
    body = json.dumps({"content": json.dumps({"text": text})}).encode()
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("code") == 0
    except urllib.error.HTTPError as exc:
        log.warning("feishu reminder reply failed: %s", exc)
        return False


def check_timeouts_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = cal.get_poller_state("escalation_timeout")
    reminded: set[str] = set(state.get("reminded_ids") or [])
    newly_reminded = 0
    token = _feishu_token()

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
            if _post_thread_reply(token=str(token), message_id=str(msg_id), text=text):
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
    return {"newly_reminded": newly_reminded, "tracked": len(reminded)}


async def start_background() -> None:
    import asyncio

    interval = int(os.environ.get("CS_OPS_ESCALATION_TIMEOUT_INTERVAL_SEC", "900"))
    while True:
        try:
            check_timeouts_once()
        except Exception as exc:
            log.warning("escalation timeout check error: %s", exc)
        await asyncio.sleep(interval)
