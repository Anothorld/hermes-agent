"""Poll Feishu escalation threads for operator replies and resume agent runs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import cal
from .escalation_resume import resume_escalation
from .feishu_client import (
    escalation_chat_id,
    list_container_messages,
    list_container_messages_since,
    tenant_access_token,
)
from .feishu_notify import is_system_escalation_message, notify_escalation_locked

log = logging.getLogger(__name__)

_ENV = os.environ.get("CS_OPS_ENV", "LIVE")
_POLL_SEC = int(os.environ.get("CS_OPS_FEISHU_POLL_INTERVAL_SEC", "30"))
_CHAT_PAGE_SIZE = int(os.environ.get("CS_OPS_FEISHU_CHAT_PAGE_SIZE", "50"))
_CHAT_MAX_PAGES = int(os.environ.get("CS_OPS_FEISHU_CHAT_LIST_MAX_PAGES", "30"))
_THREAD_PAGE_SIZE = int(os.environ.get("CS_OPS_FEISHU_THREAD_PAGE_SIZE", "20"))
_THREAD_MAX_PAGES = int(os.environ.get("CS_OPS_FEISHU_THREAD_LIST_MAX_PAGES", "5"))


def _is_topic_thread_id(thread_id: str) -> bool:
    """Feishu topic threads use omt_* ids; om_* message ids are not valid thread containers."""
    return thread_id.startswith("omt_")


def _list_thread_messages(*, token: str, thread_id: str) -> list[dict[str, Any]]:
    return list_container_messages(
        token=token,
        container_id_type="thread",
        container_id=thread_id,
        page_size=_THREAD_PAGE_SIZE,
        max_pages=_THREAD_MAX_PAGES,
    )


def _list_chat_messages(*, token: str, chat_id: str, since_ms: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Fetch group chat messages back to since_ms so busy chats do not hide older replies."""
    if since_ms > 0:
        return list_container_messages_since(
            token=token,
            container_id_type="chat",
            container_id=chat_id,
            since_ms=since_ms,
            page_size=_CHAT_PAGE_SIZE,
            max_pages=_CHAT_MAX_PAGES,
        )
    messages = list_container_messages(
        token=token,
        container_id_type="chat",
        container_id=chat_id,
        page_size=_CHAT_PAGE_SIZE,
        max_pages=_CHAT_MAX_PAGES,
    )
    return messages, 0


def _replies_to_root(messages: list[dict[str, Any]], root_message_id: str) -> list[dict[str, Any]]:
    """Human replies under a root post in a normal group use parent_id, not thread containers."""
    root = str(root_message_id)
    replies: list[dict[str, Any]] = []
    for msg in messages:
        mid = str(msg.get("message_id") or "")
        if not mid or mid == root:
            continue
        if str(msg.get("parent_id") or "") == root:
            replies.append(msg)
    return replies


def _list_escalation_reply_messages(
    *,
    token: str,
    feishu_chat_id: str,
    feishu_message_id: str,
    feishu_thread_id: str,
    esc_created_ms: int = 0,
    escalation_id: int | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return candidate reply messages and the listing strategy used."""
    if feishu_thread_id and _is_topic_thread_id(feishu_thread_id):
        return _list_thread_messages(token=token, thread_id=feishu_thread_id), "topic_thread"

    chat_id = feishu_chat_id or (escalation_chat_id() or "")
    root_id = feishu_message_id or feishu_thread_id
    if not chat_id or not root_id:
        return [], "missing_chat_or_root"

    chat_messages, pages_fetched = _list_chat_messages(
        token=token,
        chat_id=chat_id,
        since_ms=esc_created_ms,
    )
    replies = _replies_to_root(chat_messages, root_id)
    # #region agent log
    try:
        import urllib.request as _ur

        _payload = json.dumps(
            {
                "sessionId": "f4b5a4",
                "runId": "post-fix",
                "hypothesisId": "D",
                "location": "feishu_escalation_poller.py:_list_escalation_reply_messages",
                "message": "chat_parent reply scan",
                "data": {
                    "escalation_id": escalation_id,
                    "root_id": root_id,
                    "since_ms": esc_created_ms,
                    "pages_fetched": pages_fetched,
                    "chat_messages": len(chat_messages),
                    "replies_under_root": len(replies),
                    "reply_ids": [str(m.get("message_id") or "") for m in replies],
                },
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        ).encode()
        _req = _ur.Request(
            "http://127.0.0.1:7411/ingest/32e61462-f4f7-4538-9c62-3cdb124b8dba",
            data=_payload,
            headers={
                "Content-Type": "application/json",
                "X-Debug-Session-Id": "f4b5a4",
            },
            method="POST",
        )
        _ur.urlopen(_req, timeout=1).read()
    except Exception:
        pass
    # #endregion
    return replies, "chat_parent"


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


def _escalation_created_ms(esc: dict[str, Any]) -> int:
    try:
        from datetime import datetime

        return int(
            datetime.fromisoformat(str(esc.get("created_at", "")).replace("Z", "+00:00")).timestamp() * 1000
        )
    except (ValueError, TypeError):
        return 0


def _is_valid_operator_reply(msg: dict[str, Any], *, esc_created_ms: int) -> bool:
    mid = str(msg.get("message_id") or "")
    if not mid:
        return False
    if esc_created_ms and _msg_ts(msg) <= esc_created_ms:
        return False
    sender = msg.get("sender") or {}
    if sender.get("sender_type") == "app":
        return False
    text = _message_text(msg).strip()
    return bool(text) and not is_system_escalation_message(text)


def _collect_operator_replies(
    messages: list[dict[str, Any]],
    *,
    esc_created_ms: int,
) -> list[dict[str, Any]]:
    """Return valid human replies sorted oldest-first."""
    replies = [msg for msg in messages if _is_valid_operator_reply(msg, esc_created_ms=esc_created_ms)]
    replies.sort(key=_msg_ts)
    return replies


def _pick_first_operator_reply(
    messages: list[dict[str, Any]],
    *,
    esc_created_ms: int,
    seen_message_id: str,
) -> dict[str, Any] | None:
    """Return the earliest valid human reply (first-reply-wins)."""
    candidates = _collect_operator_replies(messages, esc_created_ms=esc_created_ms)
    for msg in candidates:
        mid = str(msg.get("message_id") or "")
        if mid and mid != seen_message_id:
            return msg
    return None


def _ensure_escalation_lock_notified(
    *,
    escalation_id: int,
    feishu_root_message_id: str,
    token: str,
    resume_context: dict[str, Any] | None = None,
) -> bool:
    """Post [ESC-LOCK:…] once; persist feishu_lock_notified for retries while resuming."""
    ctx = resume_context or {}
    if ctx.get("feishu_lock_notified"):
        return True
    if not feishu_root_message_id:
        return False
    lock = notify_escalation_locked(
        escalation_id=escalation_id,
        feishu_root_message_id=feishu_root_message_id,
        token=token,
    )
    if not lock.ok:
        log.warning("feishu lock notify failed esc=%s: %s", escalation_id, lock.error)
        return False
    cal.merge_escalation_resume_context(
        escalation_id=escalation_id,
        patch={"feishu_lock_notified": True, "feishu_lock_message_id": lock.message_id},
    )
    if resume_context is not None:
        resume_context["feishu_lock_notified"] = True
        resume_context["feishu_lock_message_id"] = lock.message_id
    return True


def _poll_resuming_escalations(
    *,
    token: str,
    env: str,
    seen_late: dict[str, list[str]],
) -> dict[str, int]:
    """Retry LOCK if missing; track late operator replies under claimed escalations."""
    locks_retried = 0
    late_detected = 0
    for esc in cal.list_escalations(state="resuming", env=env):
        eid = str(esc["id"])
        ctx = esc.get("resume_context") or {}
        winning_mid = str(ctx.get("feishu_reply_message_id") or "")
        feishu_chat_id = str(esc.get("feishu_chat_id") or "")
        feishu_message_id = str(esc.get("feishu_message_id") or "")
        feishu_thread_id = str(esc.get("feishu_thread_id") or "")
        root_for_lock = feishu_message_id or feishu_thread_id
        if not root_for_lock and not feishu_thread_id:
            continue

        try:
            messages, listing_mode = _list_escalation_reply_messages(
                token=token,
                feishu_chat_id=feishu_chat_id,
                feishu_message_id=feishu_message_id,
                feishu_thread_id=feishu_thread_id,
                esc_created_ms=_escalation_created_ms(esc),
                escalation_id=int(eid),
            )
        except urllib.error.HTTPError as exc:
            log.warning("feishu list messages failed resuming esc=%s: %s", eid, exc)
            continue

        if listing_mode == "missing_chat_or_root":
            continue

        if root_for_lock and not ctx.get("feishu_lock_notified"):
            if _ensure_escalation_lock_notified(
                escalation_id=int(eid),
                feishu_root_message_id=root_for_lock,
                token=token,
                resume_context=ctx,
            ):
                locks_retried += 1

        esc_created_ms = _escalation_created_ms(esc)
        late_ids = seen_late.setdefault(eid, [])
        for msg in _collect_operator_replies(messages, esc_created_ms=esc_created_ms):
            mid = str(msg.get("message_id") or "")
            if not mid or mid == winning_mid or mid in late_ids:
                continue
            late_ids.append(mid)
            late_detected += 1
            log.info("late operator reply ignored esc=%s msg=%s", eid, mid)
            if root_for_lock:
                _ensure_escalation_lock_notified(
                    escalation_id=int(eid),
                    feishu_root_message_id=root_for_lock,
                    token=token,
                    resume_context=ctx,
                )

    return {"locks_retried": locks_retried, "late_detected": late_detected}


def _retry_resuming_without_run(*, env: str) -> int:
    """Retry gateway launch for claimed escalations when a prior launch failed."""
    retried = 0
    for esc in cal.list_escalations(state="resuming", env=env):
        ctx = esc.get("resume_context") or {}
        if ctx.get("resume_run_id"):
            continue
        eid = int(esc["id"])
        answer = str(ctx.get("operator_answer_raw") or esc.get("operator_answer") or "").strip()
        if not answer:
            continue
        result = resume_escalation(
            escalation_id=eid,
            operator_answer=answer,
            decided_by=str(esc.get("decided_by") or "feishu_operator"),
            env=env,
            already_claimed=True,
        )
        if result.get("ok"):
            retried += 1
        else:
            log.warning("retry resume escalation %s failed: %s", eid, result.get("error"))
    return retried


def poll_once() -> dict[str, Any]:
    token = tenant_access_token()
    if not token:
        return {"error": "missing FEISHU_APP_ID/SECRET", "resumed": 0}

    state = cal.get_poller_state("feishu_escalation_poller")
    seen: dict[str, str] = dict(state.get("seen_replies") or {})
    seen_late: dict[str, list[str]] = {
        str(k): list(v) for k, v in (state.get("seen_late_replies") or {}).items()
    }
    resumed = 0

    for esc in cal.list_escalations(state="awaiting_answer", env=_ENV):
        eid = str(esc["id"])
        feishu_chat_id = str(esc.get("feishu_chat_id") or "")
        feishu_message_id = str(esc.get("feishu_message_id") or "")
        feishu_thread_id = str(esc.get("feishu_thread_id") or "")
        if not feishu_message_id and not feishu_thread_id:
            continue

        esc_created_ms = _escalation_created_ms(esc)
        try:
            messages, listing_mode = _list_escalation_reply_messages(
                token=token,
                feishu_chat_id=feishu_chat_id,
                feishu_message_id=feishu_message_id,
                feishu_thread_id=feishu_thread_id,
                esc_created_ms=_escalation_created_ms(esc),
                escalation_id=int(eid),
            )
        except urllib.error.HTTPError as exc:
            log.warning("feishu list messages failed esc=%s: %s", eid, exc)
            continue

        if listing_mode == "missing_chat_or_root":
            continue

        msg = _pick_first_operator_reply(
            messages,
            esc_created_ms=esc_created_ms,
            seen_message_id=seen.get(eid, ""),
        )
        if not msg:
            continue

        mid = str(msg.get("message_id") or "")
        text = _message_text(msg).strip()
        sender = msg.get("sender") or {}
        root_for_lock = feishu_message_id or feishu_thread_id

        if not cal.claim_escalation_reply(
            escalation_id=int(eid),
            operator_answer=text,
            decided_by=str(sender.get("id") or "feishu_operator"),
            feishu_reply_message_id=mid,
        ):
            continue

        if root_for_lock:
            _ensure_escalation_lock_notified(
                escalation_id=int(eid),
                feishu_root_message_id=root_for_lock,
                token=token,
            )

        result = resume_escalation(
            escalation_id=int(eid),
            operator_answer=text,
            decided_by=str(sender.get("id") or "feishu_operator"),
            env=_ENV,
            feishu_reply_message_id=mid,
            already_claimed=True,
            feishu_messages=messages,
            feishu_token=token,
            exclude_feishu_message_ids={mid},
            feishu_after_ms=esc_created_ms,
        )
        if not result.get("ok"):
            log.error("resume escalation %s failed: %s", eid, result.get("error"))
            continue
        seen[eid] = mid
        resumed += 1

    retried = _retry_resuming_without_run(env=_ENV)
    resuming_stats = _poll_resuming_escalations(token=token, env=_ENV, seen_late=seen_late)

    cal.set_poller_state(
        "feishu_escalation_poller",
        {
            "seen_replies": seen,
            "seen_late_replies": seen_late,
            "last_run": time.time(),
            "resumed": resumed,
            "retried": retried,
            **resuming_stats,
        },
    )
    return {
        "resumed": resumed,
        "retried": retried,
        "tracked_escalations": len(seen),
        **resuming_stats,
    }


async def start_background() -> None:
    while True:
        try:
            poll_once()
        except Exception as exc:
            log.warning("feishu poller error: %s", exc)
        await asyncio.sleep(_POLL_SEC)
