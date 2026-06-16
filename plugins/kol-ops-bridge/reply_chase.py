"""Deterministic follow-up (chase) reply policy.

When a KOL/agency sends a new inbound while an older ``approval.reply_draft`` is
still pending (or approved-but-unsent), the dispatcher must supersede the stale
draft with one anchored to the latest message — not ``pending_action`` alone.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Literal, Mapping, Optional

from . import reply_draft

ChaseAction = Literal[
    "proceed_normal",
    "skip_same_source",
    "regenerate",
    "escalate_thread_fork",
    "defer_escalation",
]

_DEFERRABLE_ACTIONS = frozenset({"regenerate", "escalate_thread_fork"})

_PENDING_DECISIONS = frozenset({None, "pending"})


def _non_empty(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_iso(raw: str | None) -> _dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        normalized = raw.strip().replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except ValueError:
        return None


def _draft_source_message_id(fact: Mapping[str, Any]) -> str | None:
    _, source_message_id = reply_draft.extract_thread_anchors(fact)
    return source_message_id


def _draft_thread_id(fact: Mapping[str, Any]) -> str | None:
    thread_id, _ = reply_draft.extract_thread_anchors(fact)
    return thread_id


def _cold_outreach_first_gmail_reply(
    *,
    prior_thread: str | None,
    prior_source: str | None,
    inbound_thread_id: str | None,
) -> bool:
    """True when KOL replied on a real Gmail thread after a cold-outreach synthetic anchor."""
    if not inbound_thread_id or reply_draft.is_cold_outreach_anchor(inbound_thread_id):
        return False
    return bool(
        reply_draft.is_cold_outreach_anchor(prior_thread)
        or reply_draft.is_cold_outreach_anchor(prior_source)
    )


def _threads_linked(
    *,
    draft_thread_id: str | None,
    inbound_thread_id: str | None,
    event_thread_ids: set[str],
) -> bool:
    """Return True when it is safe to auto-regenerate across threads."""
    if not draft_thread_id or not inbound_thread_id:
        return True
    if draft_thread_id == inbound_thread_id:
        return True
    if draft_thread_id in event_thread_ids and inbound_thread_id in event_thread_ids:
        return True
    return False


def evaluate_chase(
    *,
    reply_draft_fact: Mapping[str, Any] | None,
    reply_draft_captured_at: str | None,
    inbound_message_id: str,
    inbound_thread_id: str | None,
    event_thread_ids: Optional[set[str]] = None,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Return chase decision + operator/dispatcher context payload."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    threads = event_thread_ids or set()
    base: dict[str, Any] = {
        "prior_pending_draft": False,
        "prior_source_message_id": None,
        "prior_thread_id": None,
        "recommended_action": "proceed_normal",
        "stale_hours": None,
        "inbound_message_id": inbound_message_id,
        "inbound_thread_id": inbound_thread_id,
    }
    if not reply_draft_fact or not isinstance(reply_draft_fact, dict):
        return base

    decision = reply_draft_fact.get("decision")
    prior_source = _draft_source_message_id(reply_draft_fact)
    prior_thread = _draft_thread_id(reply_draft_fact)
    captured = _parse_iso(reply_draft_captured_at)
    if captured is not None:
        base["stale_hours"] = round(
            max(0.0, (now - captured).total_seconds()) / 3600.0,
            2,
        )

    if prior_source:
        base["prior_source_message_id"] = prior_source
    if prior_thread:
        base["prior_thread_id"] = prior_thread

    if prior_source == inbound_message_id:
        base["recommended_action"] = "skip_same_source"
        return base

    if decision == "rejected":
        return base

    is_pending = decision in _PENDING_DECISIONS
    is_approved_unsent = (
        decision == "approved"
        and isinstance(reply_draft_fact.get("gmail_draft"), dict)
        and prior_source
        and prior_source != inbound_message_id
    )
    if not is_pending and not is_approved_unsent:
        return base

    base["prior_pending_draft"] = is_pending
    if is_approved_unsent:
        base["prior_approved_unsent"] = True

    if _cold_outreach_first_gmail_reply(
        prior_thread=prior_thread,
        prior_source=prior_source,
        inbound_thread_id=inbound_thread_id,
    ):
        base["recommended_action"] = "regenerate"
        base["chase_note"] = "cold_outreach_first_gmail_reply"
        return base

    if _threads_linked(
        draft_thread_id=prior_thread,
        inbound_thread_id=inbound_thread_id,
        event_thread_ids=threads,
    ):
        base["recommended_action"] = "regenerate"
    else:
        base["recommended_action"] = "escalate_thread_fork"
    return base


def apply_open_escalation_defer(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """When an escalation is awaiting operator answer, suppress chase drafting."""
    out = dict(evaluation)
    action = out.get("recommended_action")
    if action not in _DEFERRABLE_ACTIONS:
        return out
    out["recommended_action"] = "defer_escalation"
    out["defer_reason"] = "open_escalation_awaiting_answer"
    out["deferred_chase_action"] = action
    return out


def chase_context_from_evaluation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Shape stored on ``pending_replies[i].chase_context``."""
    ctx: dict[str, Any] = {
        "prior_pending_draft": bool(evaluation.get("prior_pending_draft")),
        "prior_approved_unsent": bool(evaluation.get("prior_approved_unsent")),
        "prior_source_message_id": evaluation.get("prior_source_message_id"),
        "prior_thread_id": evaluation.get("prior_thread_id"),
        "recommended_action": evaluation.get("recommended_action") or "proceed_normal",
        "stale_hours": evaluation.get("stale_hours"),
        "inbound_message_id": evaluation.get("inbound_message_id"),
        "inbound_thread_id": evaluation.get("inbound_thread_id"),
    }
    if evaluation.get("defer_reason"):
        ctx["defer_reason"] = evaluation.get("defer_reason")
    if evaluation.get("deferred_chase_action"):
        ctx["deferred_chase_action"] = evaluation.get("deferred_chase_action")
    if evaluation.get("chase_note"):
        ctx["chase_note"] = evaluation.get("chase_note")
    return ctx


__all__ = [
    "ChaseAction",
    "evaluate_chase",
    "apply_open_escalation_defer",
    "chase_context_from_evaluation",
]
