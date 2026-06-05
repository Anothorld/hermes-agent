"""Gmail reply-all and quoted-reply helpers for approval draft creation.

When an operator approves ``approval.reply_draft``, the bridge must mirror
Gmail web Reply / Reply all: reply-all Cc, ``In-Reply-To`` on the inbound
message, and a ``gmail_quote_container`` block containing that message's full
body (including any nested quotes already embedded in the MIME part).
"""

from __future__ import annotations

import html as html_module
import re
from email.utils import formataddr, getaddresses, parseaddr

_WROTE_MARKERS = (
    " wrote:",
    " 写道：",
)
_QUOTE_LINE = re.compile(r"^>.*$", re.MULTILINE)
_ON_WROTE_SPLIT = re.compile(r"(?ms)^\s*On .+ wrote:\s*$")
_HTML_TAG = re.compile(r"<[^>]+>")
_BODY_TAG = re.compile(r"(?is)<\s*body[^>]*>(.*)</\s*body\s*>")
_HTML_WRAPPER = re.compile(r"(?is)^\s*<html[^>]*>.*</html>\s*$")


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
    if "gmail_quote_container" in lower or 'class="gmail_quote"' in lower:
        return True
    if "gmail_extra" in lower:
        return True
    if any(marker in lower for marker in _WROTE_MARKERS):
        return True
    if _QUOTE_LINE.search(text):
        return True
    return False


def _strip_html_tags(text: str) -> str:
    if not text:
        return ""
    unescaped = html_module.unescape(text)
    return _HTML_TAG.sub(" ", unescaped)


def extract_message_content_without_quotes(text: str) -> str:
    """Return only the new content of a message, not nested thread quotes.

    Used when persisting child draft envelopes — not when building the
    Gmail-native approve-time quote block.
    """
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        return ""
    if "<" in body and ">" in body:
        body = _strip_html_tags(body)
    body = _ON_WROTE_SPLIT.split(body, maxsplit=1)[0]
    body = _QUOTE_LINE.sub("", body)
    return body.strip()


def plain_body_to_html(text: str) -> str:
    """Escape plain text and preserve line breaks for Gmail HTML drafts."""
    escaped = (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return escaped.replace("\n", "<br>\n")


def _html_fragment(text: str) -> str:
    """Return an embeddable HTML fragment (strip document wrappers)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    body_match = _BODY_TAG.search(raw)
    if body_match:
        raw = body_match.group(1).strip()
    elif _HTML_WRAPPER.match(raw):
        raw = _HTML_TAG.sub("", raw).strip()
    return raw


def _format_gmail_attr_line(*, from_header: str, date_line: str) -> str:
    """Format the ``On … wrote:`` line like Gmail web Reply."""
    from_header = (from_header or "unknown sender").strip()
    date_line = (date_line or "").strip()
    email = extract_email(from_header)
    if date_line and email:
        name = from_header.rsplit("<", 1)[0].strip().strip('"') if "<" in from_header else from_header
        name_esc = html_module.escape(name or email)
        email_esc = html_module.escape(email)
        date_esc = html_module.escape(date_line)
        return (
            f"On {date_esc}, {name_esc} "
            f'<span dir="ltr">&lt;<a href="mailto:{email_esc}" target="_blank">'
            f"{email_esc}</a>&gt;</span> wrote:<br>"
        )
    if date_line:
        return f"On {html_module.escape(date_line)}, {html_module.escape(from_header)} wrote:<br>"
    return f"{html_module.escape(from_header)} wrote:<br>"


def _resolve_quote_inner(
    *,
    quoted_body_html: str,
    quoted_body_plain: str,
    quoted_body_fallback: str = "",
) -> str:
    """Pick embeddable parent HTML; prefer exact MIME HTML for Gmail collapse."""
    inner = _html_fragment(quoted_body_html)
    if inner:
        return inner
    fallback = (quoted_body_plain or quoted_body_fallback or "").strip()
    if fallback and "<" in fallback and ">" in fallback and _HTML_TAG.search(fallback):
        inner = _html_fragment(fallback)
        if inner:
            return inner
    if fallback:
        return f'<div dir="ltr">{plain_body_to_html(fallback)}</div>'
    return ""


def build_gmail_native_reply_html(
    *,
    new_body: str,
    quoted_from: str,
    quoted_date: str,
    quoted_body_html: str = "",
    quoted_body_plain: str = "",
) -> str:
    """Compose an HTML body matching Gmail web Reply / Reply all.

    Uses ``gmail_extra`` + ``gmail_quote`` wrappers (same as Gmail-sent replies)
    so the web UI shows the collapsible ``…`` control on the quoted block.
    """
    new_html = plain_body_to_html((new_body or "").strip())
    quote_inner = _resolve_quote_inner(
        quoted_body_html=quoted_body_html,
        quoted_body_plain=quoted_body_plain,
    )
    if not quote_inner:
        return f'<div dir="ltr">{new_html}</div>' if new_html else ""

    attr = _format_gmail_attr_line(from_header=quoted_from, date_line=quoted_date)

    parts = [f'<div dir="ltr">{new_html}</div>'] if new_html else []
    parts.extend([
        '<div class="gmail_extra"><br>',
        '<div class="gmail_quote">',
        f'<div dir="ltr" class="gmail_attr">{attr}</div>',
        (
            '<blockquote class="gmail_quote" type="cite" '
            'style="margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex">'
        ),
        quote_inner,
        "</blockquote>",
        "</div>",
        "<br></div>",
    ])
    return "".join(parts)


__all__ = [
    "body_has_quoted_reply",
    "build_gmail_native_reply_html",
    "compute_reply_all_cc",
    "extract_email",
    "extract_message_content_without_quotes",
    "parse_recipient_header",
    "plain_body_to_html",
]
