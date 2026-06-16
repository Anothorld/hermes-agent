"""Detect automated inbound replies (DSN bounces, OOO, mailer-daemon)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..gmail_client import is_bounce_body

_AUTOMATED_SENDER_RE = re.compile(
    r"(mailer-daemon|mail delivery subsystem|postmaster@|bounce@|"
    r"noreply|no-reply|donotreply|do-not-reply)",
    re.IGNORECASE,
)
_AUTO_REPLY_TEXT_RE = re.compile(
    r"(out of office|automatic reply|auto reply|auto-reply|autoreply|"
    r"away from (?:the )?office|vacation reply|"
    r"delivery status notification \(failure\)|"
    r"i am currently out of|i'?m out of the office|"
    r"will respond when i return|will get back to you when i return)",
    re.IGNORECASE,
)


def is_automated_inbound_reply_payload(payload: Mapping[str, Any]) -> bool:
    """Return True when an inbound reply is a bounce DSN or auto-response."""
    from_addr = str(payload.get("from_addr") or "")
    subject = str(payload.get("subject") or "")
    body = str(payload.get("body") or "")
    snippet = str(payload.get("snippet") or "")
    combined_body = "\n".join(part for part in (body, snippet) if part)

    if is_bounce_body(combined_body, from_addr=from_addr):
        return True
    if _AUTOMATED_SENDER_RE.search(from_addr):
        return True

    haystack = f"{subject}\n{combined_body}"
    return bool(_AUTO_REPLY_TEXT_RE.search(haystack))
