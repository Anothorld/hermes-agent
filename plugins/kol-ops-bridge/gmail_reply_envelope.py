"""Gmail reply-all and quoted-reply helpers for approval draft creation.

When an operator approves ``approval.reply_draft``, the bridge must mirror
Gmail's Reply / Reply all behaviour: include other thread recipients in Cc
and append the prior message as a quoted block below the new body.
"""

from __future__ import annotations

import re
from email.utils import formataddr, getaddresses, parseaddr

_WROTE_MARKERS = (
    " wrote:",
    " 写道：",
)
_QUOTE_LINE = re.compile(r"^>.*$", re.MULTILINE)


def extract_email(value: str | None) -> str | None:
    """Return the bare lowercased address from a From/To/Cc header value."""
    if not value:
        return None
    _, addr = parseaddr(value)
    addr = (addr or "").strip().lower()
    return addr or None


def parse_recipient_header(header: str | None) -> list[tuple[str, str]]:
    """Parse a To/Cc header into ``(display_name, email_lower)`` pairs."""
    if not header or not header.strip():
        return []
    out: list[tuple[str, str]] = []
    for name, addr in getaddresses([header]):
        email = (addr or "").strip().lower()
        if email:
            out.append((name.strip(), email))
    return out


def compute_reply_all_cc(
    *,
    inbound_from: str,
    inbound_to: str,
    inbound_cc: str,
    reply_to: str,
    self_emails: set[str] | frozenset[str],
) -> str:
    """Build a comma-separated Cc header for Gmail Reply all.

    Recipients are everyone on the inbound To/Cc except the authenticated
    account(s) and the primary reply recipient (inbound sender).
    """
    reply_to_email = extract_email(reply_to)
    exclude = {e for e in self_emails if e} | ({reply_to_email} if reply_to_email else set())
    seen: set[str] = set()
    cc_parts: list[str] = []
    for header in (inbound_to, inbound_cc):
        for name, email in parse_recipient_header(header):
            if email in exclude or email in seen:
                continue
            seen.add(email)
            cc_parts.append(formataddr((name, email)) if name else email)
    return ", ".join(cc_parts)


def body_has_quoted_reply(body: str) -> bool:
    """True when the body already looks like it includes a quoted prior message."""
    text = (body or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(marker in lower for marker in _WROTE_MARKERS):
        return True
    if _QUOTE_LINE.search(text):
        return True
    return False


def _plain_quote_lines(text: str) -> str:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def append_quoted_reply(
    *,
    body: str,
    quoted_from: str,
    quoted_date: str,
    quoted_body: str,
    html: bool = False,
) -> str:
    """Append a Gmail-style quoted block under the new reply body."""
    new_body = (body or "").rstrip()
    quote_src = (quoted_body or "").strip()
    if not quote_src:
        return new_body
    from_line = (quoted_from or "unknown sender").strip()
    date_line = (quoted_date or "").strip()
    if html:
        header = (
            f'<div class="gmail_quote">On {date_line}, {from_line} wrote:<br>'
            if date_line
            else f'<div class="gmail_quote">{from_line} wrote:<br>'
        )
        escaped = (
            quote_src.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>\n")
        )
        quoted = f"{header}<blockquote type=\"cite\">{escaped}</blockquote></div>"
        sep = "<br><br>" if new_body else ""
        return f"{new_body}{sep}{quoted}" if new_body else quoted
    header = (
        f"On {date_line}, {from_line} wrote:\n"
        if date_line
        else f"{from_line} wrote:\n"
    )
    quoted = _plain_quote_lines(quote_src)
    sep = "\n\n" if new_body else ""
    return f"{new_body}{sep}{header}{quoted}" if new_body else f"{header}{quoted}"


__all__ = [
    "append_quoted_reply",
    "body_has_quoted_reply",
    "compute_reply_all_cc",
    "extract_email",
    "parse_recipient_header",
]
