"""Deterministic escalations for per-operator Gmail anomalies."""

from __future__ import annotations

from typing import Any, Optional

from . import cal

REASON_MAILBOX_MISMATCH = "inbound_mailbox_mismatch"


def find_open_mailbox_mismatch(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    message_id: str,
) -> Optional[int]:
    for esc in cal.list_escalations(state="awaiting_answer", env=env):
        if int(esc.get("identity_id") or 0) != identity_id:
            continue
        if str(esc.get("campaign_id") or "") != campaign_id:
            continue
        if esc.get("reason") != REASON_MAILBOX_MISMATCH:
            continue
        ctx = esc.get("resume_context") if isinstance(esc.get("resume_context"), dict) else {}
        if str(ctx.get("source_message_id") or "") == message_id:
            return int(esc["id"])
    return None


def ensure_mailbox_mismatch_escalation(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    message_id: str,
    thread_id: Optional[str],
    bound_mailbox_email: str,
    detected_mailbox_email: str,
) -> Optional[int]:
    """Open (or return existing) escalation when inbound hits a non-bound inbox."""
    existing = find_open_mailbox_mismatch(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        message_id=message_id,
    )
    if existing is not None:
        return existing
    question = (
        f"KOL reply arrived at {detected_mailbox_email}, but this campaign's "
        f"bound outbound mailbox is {bound_mailbox_email}. Take over the mailbox "
        f"in Console or ask the KOL to reply to the bound address before auto-drafting."
    )
    resume_context: dict[str, Any] = {
        "source_message_id": message_id,
        "thread_id": thread_id,
        "bound_mailbox_email": bound_mailbox_email,
        "detected_mailbox_email": detected_mailbox_email,
        "block_auto_reply_draft": True,
    }
    return cal.open_escalation(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        reason=REASON_MAILBOX_MISMATCH,
        severity="normal",
        question_to_operator=question,
        resume_context=resume_context,
        goal="outreach",
    )
