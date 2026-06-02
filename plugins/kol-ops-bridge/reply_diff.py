"""Normalize outbound email bodies and compute edit distance for learning."""

from __future__ import annotations

import difflib
import html
import re
from typing import Any

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_QUOTE_LINE = re.compile(r"^>.*$", re.MULTILINE)
_ON_WROTE = re.compile(
    r"(?ms)^\s*On .+ wrote:\s*$|^[\s-]*From: .+$|^[\s-]*Sent: .+$",
)


def strip_html(text: str) -> str:
    """Best-effort HTML → plain text."""
    if not text:
        return ""
    unescaped = html.unescape(text)
    without_tags = _HTML_TAG.sub(" ", unescaped)
    return without_tags


def normalize_email_body(text: str) -> str:
    """Normalize body for fair diff (strip quotes, html, excess whitespace)."""
    if not text:
        return ""
    body = strip_html(text)
    body = _ON_WROTE.split(body)[0]
    body = _QUOTE_LINE.sub("", body)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = _WS.sub(" ", body)
    body = _BLANK_LINES.sub("\n\n", body)
    return body.strip()


def compute_edit_distance(agent_body: str, sent_body: str) -> float:
    """Return normalized Levenshtein-like ratio in ``[0.0, 1.0]``.

    ``0.0`` means identical after normalization; ``1.0`` means completely
    different.
    """
    a = normalize_email_body(agent_body)
    b = normalize_email_body(sent_body)
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return round(max(0.0, min(1.0, 1.0 - ratio)), 4)


def build_edit_learning_payload(
    *,
    agent_body: str,
    sent_body: str,
    child_skill: str | None = None,
    goal: str | None = None,
    sent_message_id: str | None = None,
) -> dict[str, Any]:
    """Build a ``draft_edit_learning`` event payload."""
    distance = compute_edit_distance(agent_body, sent_body)
    norm_agent = normalize_email_body(agent_body)
    norm_sent = normalize_email_body(sent_body)
    return {
        "agent_body": agent_body,
        "sent_body": sent_body,
        "normalized_agent_body": norm_agent,
        "normalized_sent_body": norm_sent,
        "edit_distance": distance,
        "was_edited": distance > 0.05,
        "child_skill": child_skill or "",
        "goal": goal or "",
        "sent_message_id": sent_message_id or "",
    }
