"""Build KOL email conversation history from Gmail (sent + received, no drafts)."""

from __future__ import annotations

from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Optional

from . import cal
from .gmail_client import GmailClient, GmailUnavailable

# Cap messages returned to the console (per identity+campaign pull).
_MAX_MESSAGES = 200


def _extract_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    _, addr = parseaddr(value)
    cleaned = addr.strip().lower()
    return cleaned or None


def _parse_date(value: Optional[str]) -> Optional[str]:
    """Return ISO timestamp for sorting; fall back to raw string."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.isoformat(timespec="seconds")
        return dt.isoformat(timespec="seconds")
    except (TypeError, ValueError, IndexError):
        return value


def _labels(msg: dict[str, Any]) -> set[str]:
    raw = msg.get("labels")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw}


def _is_draft(msg: dict[str, Any]) -> bool:
    return "DRAFT" in _labels(msg)


def _is_sent(msg: dict[str, Any]) -> bool:
    return "SENT" in _labels(msg)


def collect_thread_ids(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    facts: dict[str, Any],
    timeline_limit: int = 200,
) -> list[str]:
    """Thread IDs tied to this campaign (facts first, then inbound events)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for key in ("offer.gmail_sent_thread_id", "offer.gmail_thread_id"):
        raw = facts.get(key)
        if isinstance(raw, str) and raw.strip():
            tid = raw.strip()
            if tid not in seen:
                seen.add(tid)
                ordered.append(tid)
    events = cal.list_events(
        env=env,
        identity_id=identity_id,
        campaign_id=campaign_id,
        limit=timeline_limit,
    )
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("event_type") != "kol_inbound_reply":
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        raw_tid = payload.get("thread_id")
        if isinstance(raw_tid, str) and raw_tid.strip():
            tid = raw_tid.strip()
            if tid not in seen:
                seen.add(tid)
                ordered.append(tid)
    return ordered


def classify_gmail_message(
    msg: dict[str, Any],
    *,
    self_email: Optional[str],
    kol_email: Optional[str],
) -> Optional[tuple[str, str]]:
    """Return ``(direction, status)`` or None when the row should be skipped."""
    if _is_draft(msg):
        return None
    from_email = _extract_email(str(msg.get("from") or ""))
    if self_email and from_email == self_email:
        if _is_sent(msg):
            return ("outbound", "sent")
        return None
    if kol_email and from_email == kol_email:
        return ("inbound", "received")
    return None


def shape_gmail_message(
    msg: dict[str, Any],
    *,
    direction: str,
    status: str,
) -> dict[str, Any]:
    body = str(msg.get("body") or "").strip()
    return {
        "message_id": str(msg.get("id") or "") or None,
        "ts": _parse_date(str(msg.get("date") or "") or None),
        "event_type": "gmail_message",
        "label": "KOL 来信" if direction == "inbound" else "我方已发送",
        "direction": direction,
        "status": status,
        "from_addr": str(msg.get("from") or "") or None,
        "to_addr": str(msg.get("to") or "") or None,
        "subject": str(msg.get("subject") or "") or None,
        "body": body or None,
        "thread_id": None,
        "source": "gmail",
        "body_is_snippet": False,
    }


def build_email_conversation(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    client: GmailClient,
    facts: Optional[dict[str, Any]] = None,
    mailbox_binding: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Load sent/received Gmail messages for one identity+campaign.

    Outbound rows require the Gmail ``SENT`` label (final sends only).
    Drafts and unsent composer state are excluded.
    """
    identity = cal.get_identity(identity_id)
    if not identity:
        return {
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "env": env,
            "messages": [],
            "count": 0,
            "gmail_available": client.is_available(),
            "thread_ids": [],
            "truncated": False,
            "error": "identity_not_found",
        }
    kol_email = _extract_email(str(identity.get("primary_email") or ""))
    self_email = client.get_profile_email()
    fact_map = facts if facts is not None else cal.latest_facts_for(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if not isinstance(fact_map, dict):
        fact_map = {}

    thread_ids = collect_thread_ids(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        facts=fact_map,
    )
    if not client.is_available():
        return {
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "env": env,
            "messages": [],
            "count": 0,
            "gmail_available": False,
            "thread_ids": thread_ids,
            "truncated": False,
            "error": "gmail_unavailable",
        }

    by_id: dict[str, dict[str, Any]] = {}
    truncated = False
    for thread_id in thread_ids:
        thread = client.get_thread(thread_id)
        for msg in thread:
            if not isinstance(msg, dict):
                continue
            classified = classify_gmail_message(
                msg, self_email=self_email, kol_email=kol_email,
            )
            if classified is None:
                continue
            direction, status = classified
            mid = str(msg.get("id") or "")
            if not mid:
                continue
            shaped = shape_gmail_message(msg, direction=direction, status=status)
            shaped["thread_id"] = thread_id
            by_id[mid] = shaped
            if len(by_id) >= _MAX_MESSAGES:
                truncated = True
                break
        if truncated:
            break

    messages = sorted(
        by_id.values(),
        key=lambda r: (str(r.get("ts") or ""), str(r.get("message_id") or "")),
    )
    out: dict[str, Any] = {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "messages": messages,
        "count": len(messages),
        "gmail_available": True,
        "thread_ids": thread_ids,
        "truncated": truncated,
    }
    if mailbox_binding:
        out["mailbox"] = mailbox_binding
    return out


def build_email_conversation_safe(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    client: Optional[GmailClient] = None,
    mailbox_binding: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Like :func:`build_email_conversation` but never raises Gmail errors."""
    gmail = client or GmailClient()
    try:
        return build_email_conversation(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            client=gmail,
            mailbox_binding=mailbox_binding,
        )
    except GmailUnavailable:
        return {
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "env": env,
            "messages": [],
            "count": 0,
            "gmail_available": False,
            "thread_ids": [],
            "truncated": False,
            "error": "gmail_unavailable",
        }
