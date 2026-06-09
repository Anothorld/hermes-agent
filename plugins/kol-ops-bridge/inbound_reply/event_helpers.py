"""Event parsing helpers for identity matching."""

from __future__ import annotations

import datetime as dt
from email.utils import parseaddr
from typing import Any, Optional


def extract_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    _, addr = parseaddr(value)
    addr = (addr or "").strip().lower()
    return addr or None


def normalize_subject(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    while raw.startswith(("re:", "fw:", "fwd:")):
        raw = raw.split(":", 1)[-1].strip()
    return raw


def event_message_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("message_id", "source_message_id"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            ids.add(val)
    for key in ("draft", "gmail_draft"):
        block = payload.get(key)
        if isinstance(block, dict):
            val = block.get("message_id")
            if isinstance(val, str) and val:
                ids.add(val)
    return ids


def event_thread_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    val = payload.get("thread_id")
    if isinstance(val, str) and val:
        ids.add(val)
    for key in ("draft", "gmail_draft"):
        block = payload.get(key)
        if isinstance(block, dict):
            t = block.get("thread_id")
            if isinstance(t, str) and t:
                ids.add(t)
    return ids


def event_emails(payload: dict[str, Any]) -> set[str]:
    emails: set[str] = set()
    for key in ("to", "from", "from_addr"):
        parsed = extract_email(payload.get(key) if isinstance(payload.get(key), str) else None)
        if parsed:
            emails.add(parsed)
    draft = payload.get("draft")
    if isinstance(draft, dict):
        parsed = extract_email(draft.get("to") if isinstance(draft.get("to"), str) else None)
        if parsed:
            emails.add(parsed)
    return emails


def event_subject(payload: dict[str, Any]) -> Optional[str]:
    for key in ("subject",):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    draft = payload.get("draft")
    if isinstance(draft, dict):
        value = draft.get("subject")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_event_timestamp(raw: Any) -> Optional[dt.datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def event_timestamp_from_event(ev: dict[str, Any]) -> Optional[dt.datetime]:
    """Parse event time from CAL rows (`ts`) or legacy HTTP aliases."""
    raw = ev.get("ts") or ev.get("created_at") or ev.get("captured_at")
    return parse_event_timestamp(raw)
