"""Assemble gateway input payload for kol-reply-dispatcher runs."""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..gmail_client import GmailClient, GmailMessage, GmailUnavailable
from .deps import InboundBridgePort
from .schemas import IdentityMatch

log = logging.getLogger(__name__)

_THREAD_MSG_BODY_CAP = 4000
_THREAD_HISTORY_TOTAL_CAP = 24000


def clip_text(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated {len(text) - limit} chars]"


def mailbox_mismatch_signal(
    bridge: InboundBridgePort,
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    detected_mailbox_email: str,
) -> dict[str, Any]:
    if not campaign_id or not detected_mailbox_email:
        return {}
    facts = bridge.get_facts(
        identity_id=identity_id, campaign_id=campaign_id, env=env,
    )
    if not isinstance(facts, dict):
        return {}
    bound = str(facts.get("offer.gmail_mailbox_email") or "").strip().lower()
    if not bound or bound == detected_mailbox_email.lower():
        return {}
    return {
        "mailbox_mismatch": True,
        "bound_mailbox_email": bound,
        "detected_mailbox_email": detected_mailbox_email.lower(),
        "allow_autoflow": False,
    }


def build_thread_history(
    *,
    client: GmailClient,
    thread_id: str,
    latest_message_id: str,
) -> list[dict[str, str]]:
    try:
        raw = client.get_thread(thread_id)
    except GmailUnavailable as exc:
        log.warning(
            "thread history unavailable thread=%s msg=%s: %s",
            thread_id,
            latest_message_id,
            exc,
        )
        return []
    prior_message_count = sum(1 for item in raw if item.get("id") != latest_message_id)
    history: list[dict[str, str]] = []
    total = 0
    for item in raw:
        if item.get("id") == latest_message_id:
            continue
        body = clip_text(item.get("body", ""), _THREAD_MSG_BODY_CAP)
        entry = {
            "from": item.get("from", ""),
            "date": item.get("date", ""),
            "body": body,
        }
        total += len(body)
        if total > _THREAD_HISTORY_TOTAL_CAP and history:
            dropped_count = max(prior_message_count - len(history), 0)
            history.append({
                "from": "",
                "date": "",
                "body": f"... [history truncated: dropped {dropped_count} earlier message(s)]",
            })
            break
        history.append(entry)
    return history


def pending_reply_payload(
    bridge: InboundBridgePort,
    *,
    client: GmailClient,
    msg: GmailMessage,
    matched: IdentityMatch,
    env: str,
    mailbox_user_id: int = 0,
    mailbox_email: str = "",
) -> dict[str, Any]:
    identity_id = matched.identity_id
    campaign_id = matched.campaign_id
    if not campaign_id:
        context: dict[str, Any] = {"error": "missing_campaign_id"}
        chase_context = {"recommended_action": "proceed_normal", "prior_pending_draft": False}
    else:
        context = bridge.dispatch_context(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        )
        chase_context = bridge.reply_chase_hint(
            identity_id=identity_id,
            campaign_id=campaign_id,
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            env=env,
        )
    thread_history = build_thread_history(
        client=client,
        thread_id=matched.history_thread_id or msg.thread_id,
        latest_message_id=msg.message_id,
    )
    mismatch = mailbox_mismatch_signal(
        bridge,
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        detected_mailbox_email=mailbox_email,
    )
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "latest_email": {
            "message_id": msg.message_id,
            "thread_id": msg.thread_id,
            "from": msg.from_addr,
            "to": msg.to,
            "cc": msg.cc,
            "subject": msg.subject,
            "date": msg.date,
            "in_reply_to": msg.in_reply_to,
            "references": msg.references,
            "snippet": msg.snippet,
            "body": clip_text(msg.body),
        },
        "thread_history": thread_history,
        "detected_mailbox_user_id": mailbox_user_id or None,
        "detected_mailbox_email": mailbox_email or None,
        "anomaly_signals": {
            "thread_integrity": {
                "status": matched.thread_integrity,
                "matched_by": matched.matched_by,
                "history_thread_id": matched.history_thread_id or msg.thread_id,
            },
            "identity_integrity": {
                "status": matched.identity_integrity,
                "sender_email": matched.sender_email,
                "expected_email": matched.expected_email,
                "reasons": matched.reasons,
            },
            "content_risk": matched.content_risk,
            "risk_controls": matched.risk_controls,
            **mismatch,
        },
        "dispatch_context": context,
        "chase_context": chase_context,
    }
