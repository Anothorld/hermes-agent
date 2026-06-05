"""Reply-draft envelope enrichment (from `kol-reply-dispatcher` Step 5.5).

Reply-side child skills return *content only* — they do not know the
recipient or subject. The dispatcher must merge ``to`` / ``subject`` from the
inbound email before persisting an ``approval.reply_draft`` (the Bridge
rejects a draft missing non-empty ``subject`` / ``body`` / ``to``). That merge
is pure string handling, so it lives here instead of being hand-built by the
model each turn.

Pure: no DB, no HTTP. The server ``/reply-drafts/persist`` endpoint calls
:func:`enrich_envelope` then writes the event + approval fact atomically.
``enrich_envelope`` also strips accidental thread quotes from ``body`` so
approve can append a single Gmail quote block.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import gmail_thread_resolve
from .gmail_reply_envelope import extract_message_content_without_quotes

_RE_PREFIXES = ("re:", "re：")

_PROACTIVE_FOLLOWUP_SKILLS = frozenset({
    "kol-proactive-followup",
})


class ReplyDraftError(ValueError):
    """Raised when an envelope cannot be enriched into a sendable draft."""


def _non_empty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_thread_anchors(value: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(thread_id, source_message_id)`` candidates from a reply_draft fact.

    Prefer canonical fields inside ``draft`` / ``source_message_id``. Fall back to
    legacy top-level ``thread_id`` and ``in_reply_to`` when a synthesizer wrote
    the fact directly (techjoyce incident: ``draft.thread_id`` missing but top-level
    anchors present).
    """
    draft = value.get("draft") if isinstance(value.get("draft"), dict) else {}
    thread_id = _non_empty_str(draft.get("thread_id")) or _non_empty_str(
        value.get("thread_id"),
    )
    source_message_id = _non_empty_str(value.get("source_message_id")) or _non_empty_str(
        value.get("in_reply_to"),
    )
    return thread_id, source_message_id


def has_thread_anchor(value: Mapping[str, Any]) -> bool:
    """True when the fact carries enough data to attach a Gmail draft to a thread."""
    thread_id, source_message_id = extract_thread_anchors(value)
    return bool(thread_id or source_message_id)


_INITIAL_OUTREACH_SKILLS = frozenset({
    "kol-cold-outreach",
    "kol-reengagement-outreach",
})


def is_cold_outreach_anchor(value: str | None) -> bool:
    """True for stable CAL anchors ``draft:outreach_*`` / ``outreach_*`` (not Gmail ids)."""
    anchor = (value or "").strip()
    if not anchor:
        return False
    return anchor.startswith("draft:outreach_") or anchor.startswith("outreach_")


def is_proactive_followup_draft(value: Mapping[str, Any]) -> bool:
    """True when the draft is an operator-topic follow-up in an existing thread."""
    primary_goal = _non_empty_str(value.get("primary_goal"))
    if primary_goal == "proactive_followup":
        return True
    child_skill = _non_empty_str(value.get("child_skill")) or ""
    if child_skill in _PROACTIVE_FOLLOWUP_SKILLS:
        return True
    draft = value.get("draft") if isinstance(value.get("draft"), dict) else {}
    return draft.get("kind") == "proactive_followup"


def is_initial_outreach_draft(
    value: Mapping[str, Any],
    *,
    campaign_id: str | None = None,
    identity_id: int | None = None,
) -> bool:
    """True when approve should create a standalone Gmail draft (no thread attach).

    Cold/re-engagement first-touch outreach persists synthetic thread anchors for
    CAL idempotency. Gmail ``drafts.create`` must omit ``threadId`` for those —
    there is no prior mailbox thread yet.
    """
    if is_proactive_followup_draft(value):
        return False
    primary_goal = _non_empty_str(value.get("primary_goal"))
    if primary_goal and primary_goal != "outreach":
        return False
    child_skill = _non_empty_str(value.get("child_skill")) or ""
    if child_skill in _INITIAL_OUTREACH_SKILLS:
        return True
    draft = value.get("draft") if isinstance(value.get("draft"), dict) else {}
    if draft.get("kind") == "initial_outreach":
        return True
    thread_id, source_message_id = extract_thread_anchors(value)
    if is_cold_outreach_anchor(thread_id) or is_cold_outreach_anchor(source_message_id):
        return True
    if campaign_id and identity_id is not None:
        expected_thread = f"outreach_{campaign_id}_{identity_id}"
        expected_source = f"draft:outreach_{campaign_id}_{identity_id}"
        if thread_id == expected_thread or source_message_id == expected_source:
            return True
    return False


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
    if isinstance(body, str) and body.strip():
        # Child skills sometimes echo prior mail ("On … wrote:", ``>`` lines).
        # Strip those here — approve adds a single Gmail quote block later.
        body = extract_message_content_without_quotes(body)
        merged["body"] = body
    if not (isinstance(body, str) and body.strip()):
        raise ReplyDraftError("child envelope has empty body")

    # Thread continuity: inherit the inbound thread_id when the child omitted it.
    if not merged.get("thread_id") and latest_email.get("thread_id"):
        merged["thread_id"] = latest_email["thread_id"]
    return merged


def normalize_proactive_followup_thread(
    child_envelope: dict[str, Any],
    latest_email: dict[str, Any],
    *,
    facts: Mapping[str, Any],
    identity_id: int,
    campaign_id: str,
    env: str,
) -> None:
    """In-place: replace synthetic/missing thread anchors with a real Gmail thread id.

    Proactive follow-ups must approve into the existing outreach thread (reply
    draft), not as a standalone new email.
    """
    from . import email_conversation

    current = _non_empty_str(child_envelope.get("thread_id")) or _non_empty_str(
        latest_email.get("thread_id"),
    )
    if current and gmail_thread_resolve.is_plausible_gmail_resource_id(current):
        child_envelope.setdefault("thread_id", current)
        latest_email.setdefault("thread_id", current)
        return

    for tid in email_conversation.collect_thread_ids(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        facts=dict(facts),
    ):
        if gmail_thread_resolve.is_plausible_gmail_resource_id(tid):
            child_envelope["thread_id"] = tid
            latest_email.setdefault("thread_id", tid)
            return


def build_draft_event_payload(
    *,
    source_message_id: str,
    primary_lane: str,
    primary_goal: str,
    child_skill: str,
    merged_draft: Mapping[str, Any],
    contributing_skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the ``kol_reply_draft_ready`` event payload."""
    payload: dict[str, Any] = {
        "source_message_id": source_message_id,
        "primary_lane": primary_lane,
        "primary_goal": primary_goal,
        "child_skill": child_skill,
        "draft": dict(merged_draft),
    }
    if contributing_skills:
        payload["contributing_skills"] = list(contributing_skills)
    return payload


def build_approval_fact_value(
    *,
    source_message_id: str,
    primary_lane: str,
    primary_goal: str,
    child_skill: str,
    merged_draft: Mapping[str, Any],
    linked_escalation_id: Any = None,
    contributing_skills: list[dict[str, Any]] | None = None,
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
    if contributing_skills:
        value["contributing_skills"] = list(contributing_skills)
    if linked_escalation_id is not None:
        value["linked_escalation_id"] = linked_escalation_id
    return value


__all__ = [
    "enrich_envelope",
    "extract_thread_anchors",
    "has_thread_anchor",
    "is_cold_outreach_anchor",
    "is_initial_outreach_draft",
    "is_proactive_followup_draft",
    "normalize_proactive_followup_thread",
    "build_draft_event_payload",
    "build_approval_fact_value",
    "ReplyDraftError",
]
