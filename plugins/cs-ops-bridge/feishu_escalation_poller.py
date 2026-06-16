"""Poll Feishu escalation threads for operator replies and resume agent runs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from . import cal
from .gateway_client import GatewayClient

log = logging.getLogger(__name__)

_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
_POLL_SEC = int(os.environ.get("CS_OPS_FEISHU_POLL_INTERVAL_SEC", "30"))


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


def _list_thread_messages(*, token: str, thread_id: str, page_size: int = 20) -> list[dict[str, Any]]:
    url = (
        "https://open.feishu.cn/open-apis/im/v1/messages"
        f"?container_id_type=thread&container_id={thread_id}&page_size={page_size}"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    items = data.get("data", {}).get("items") or []
    return items if isinstance(items, list) else []


def _message_text(item: dict[str, Any]) -> str:
    body = item.get("body") or {}
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return str(parsed.get("text") or content)
            except json.JSONDecodeError:
                return content
    return ""


def _msg_ts(item: dict[str, Any]) -> int:
    raw = item.get("create_time") or item.get("created_at") or "0"
    try:
        return int(str(raw))
    except ValueError:
        return 0


def poll_once() -> dict[str, Any]:
    token = _feishu_token()
    if not token:
        return {"error": "missing FEISHU_APP_ID/SECRET", "resumed": 0}

    state = cal.get_poller_state("feishu_escalation_poller")
    seen: dict[str, str] = dict(state.get("seen_replies") or {})
    resumed = 0

    for esc in cal.list_escalations(state="awaiting_answer", env=_ENV):
        thread_id = esc.get("feishu_thread_id")
        if not thread_id:
            continue
        eid = str(esc["id"])
        esc_created_ms = 0
        try:
            from datetime import datetime

            esc_created_ms = int(
                datetime.fromisoformat(str(esc.get("created_at", "")).replace("Z", "+00:00")).timestamp() * 1000
            )
        except (ValueError, TypeError):
            pass
        try:
            messages = _list_thread_messages(token=token, thread_id=thread_id)
        except urllib.error.HTTPError as exc:
            log.warning("feishu list messages failed esc=%s: %s", eid, exc)
            continue
        for msg in reversed(messages):
            mid = str(msg.get("message_id") or "")
            if not mid or seen.get(eid) == mid:
                continue
            if esc_created_ms and _msg_ts(msg) <= esc_created_ms:
                continue
            sender = msg.get("sender") or {}
            if sender.get("sender_type") == "app":
                continue
            text = _message_text(msg).strip()
            if not text or text.startswith("[ESC:"):
                continue
            esc_full = cal.get_escalation(escalation_id=int(eid))
            qsid = ""
            if esc_full and esc_full.get("session"):
                qsid = str(esc_full["session"].get("quickcep_session_id") or "")
            if not qsid:
                log.warning("escalation %s missing quickcep session — skip", eid)
                break
            outcome = GatewayClient.from_env().start_resume_run(
                escalation_id=int(eid),
                quickcep_session_id=qsid,
                env=_ENV,
                operator_answer=text,
            )
            if not outcome.run_id:
                log.error("resume launch failed for escalation %s", eid)
                break
            cal.resolve_escalation(
                escalation_id=int(eid),
                decision="resume",
                decided_by=str(sender.get("id") or "feishu_operator"),
                operator_answer=text,
                final_state="resolved",
            )
            seen[eid] = mid
            resumed += 1
            break

    cal.set_poller_state(
        "feishu_escalation_poller",
        {"seen_replies": seen, "last_run": time.time(), "resumed": resumed},
    )
    return {"resumed": resumed, "tracked_escalations": len(seen)}


async def start_background() -> None:
    while True:
        try:
            poll_once()
        except Exception as exc:
            log.warning("feishu poller error: %s", exc)
        await asyncio.sleep(_POLL_SEC)
