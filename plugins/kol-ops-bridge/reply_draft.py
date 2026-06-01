"""Reply-draft envelope enrichment (from `kol-reply-dispatcher` Step 5.5).

Reply-side child skills return *content only* — they do not know the
recipient or subject. The dispatcher must merge ``to`` / ``subject`` from the
inbound email before persisting an ``approval.reply_draft`` (the Bridge
rejects a draft missing non-empty ``subject`` / ``body`` / ``to``). That merge
is pure string handling, so it lives here instead of being hand-built by the
model each turn.

Pure: no DB, no HTTP. The server ``/reply-drafts/persist`` endpoint calls
:func:`enrich_envelope` then writes the event + approval fact atomically.
"""

from __future__ import annotations

from typing import Any, Mapping

_RE_PREFIXES = ("re:", "re：")


class ReplyDraftError(ValueError):
    """Raised when an envelope cannot be enriched into a sendable draft."""


def _with_re_prefix(subject: str) -> str:
    subject = (subject or "").strip()
    if not subject:
        return "Re:"
    if subject.lower().startswith(_RE_PREFIXES):
        return subject
    return f"Re: {subject}"


def enrich_envelope(
    child_envelope: Mapping[str, Any],
    latest_email: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge inbound ``from``/``subject`` into a child skill's draft envelope.

    Args:
        child_envelope: the content-only envelope a reply-side child skill
            returned (``body``, optional ``thread_id``, branch metadata …).
        latest_email: the inbound email the dispatcher is replying to
            (``from``/``from_addr``, ``subject``, ``thread_id`` …).

    Returns:
        A merged envelope with non-empty ``to``, ``subject``, ``body``.

    Raises:
        ReplyDraftError: if recipient or body cannot be resolved.
    """
    merged = dict(child_envelope or {})
    # Recipient: child never sets ``to``; take it from the inbound sender.
    recipient = (
        merged.get("to")
        or latest_email.get("from")
        or latest_email.get("from_addr")
    )
    if not (isinstance(recipient, str) and recipient.strip()):
        raise ReplyDraftError("cannot resolve recipient: latest_email has no from/from_addr")
    merged["to"] = recipient.strip()

    # Subject: prefer child's explicit subject, else Re:-prefix the inbound one.
    subject = merged.get("subject") or _with_re_prefix(latest_email.get("subject", ""))
    merged["subject"] = subject

    body = merged.get("body")
    if not (isinstance(body, str) and body.strip()):
        raise ReplyDraftError("child envelope has empty body")

    # Thread continuity: inherit the inbound thread_id when the child omitted it.
    if not merged.get("thread_id") and latest_email.get("thread_id"):
        merged["thread_id"] = latest_email["thread_id"]
    return merged


def build_draft_event_payload(
    *,
    source_message_id: str,
    primary_lane: str,
    primary_goal: str,
    child_skill: str,
    merged_draft: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the ``kol_reply_draft_ready`` event payload."""
    return {
        "source_message_id": source_message_id,
        "primary_lane": primary_lane,
        "primary_goal": primary_goal,
        "child_skill": child_skill,
        "draft": dict(merged_draft),
    }


def build_approval_fact_value(
    *,
    source_message_id: str,
    primary_lane: str,
    primary_goal: str,
    child_skill: str,
    merged_draft: Mapping[str, Any],
    linked_escalation_id: Any = None,
) -> dict[str, Any]:
    """Build the ``approval.reply_draft`` fact value (decision=pending)."""
    value: dict[str, Any] = {
        "decision": "pending",
        "source_message_id": source_message_id,
        "primary_lane": primary_lane,
        "primary_goal": primary_goal,
        "child_skill": child_skill,
        "draft": dict(merged_draft),
    }
    if linked_escalation_id is not None:
        value["linked_escalation_id"] = linked_escalation_id
    return value


__all__ = [
    "enrich_envelope",
    "build_draft_event_payload",
    "build_approval_fact_value",
    "ReplyDraftError",
]
