"""Gmail SENT reconciliation — mark approved drafts sent + edit-learning capture."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import cal
from . import reply_diff
from .gmail_client import GmailClient, GmailUnavailable

log = logging.getLogger(__name__)


def run_reconcile_sent(
    *,
    env: str,
    lookback_days: int = 7,
    max_results: int = 100,
    client: Optional[GmailClient] = None,
) -> dict[str, Any]:
    """Reconcile approved reply drafts that appear in Gmail SENT.

    Returns a summary dict (also suitable for learning job audit output).
    Raises :class:`GmailUnavailable` when Gmail is not configured.
    """
    gmail = client or GmailClient()
    if not gmail.is_available():
        raise GmailUnavailable("gmail token or google_api.py unavailable")

    sent_thread_ids = gmail.list_sent_thread_ids(
        lookback_days=int(lookback_days),
        max_results=int(max_results),
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reconciled: list[dict[str, Any]] = []
    edit_learning_count = 0
    for row in cal.list_approved_reply_drafts(env=env):
        gmail_draft = row.get("gmail_draft") if isinstance(row, dict) else {}
        thread_id = gmail_draft.get("thread_id") if isinstance(gmail_draft, dict) else None
        if not thread_id or thread_id not in sent_thread_ids:
            continue
        identity_id = int(row["identity_id"])
        campaign_id = row.get("campaign_id")
        approval_value = row.get("value") if isinstance(row.get("value"), dict) else {}
        draft_obj = approval_value.get("draft") if isinstance(approval_value.get("draft"), dict) else {}
        agent_body = str(draft_obj.get("body") or "")
        goal = str(approval_value.get("primary_goal") or "outreach")
        lane = str(approval_value.get("primary_lane") or "commerce")
        child_skill = str(approval_value.get("child_skill") or "")
        sent_body = ""
        sent_message_id = ""
        try:
            sent_body, sent_message_id = gmail.resolve_sent_body(
                thread_id=str(thread_id),
                preferred_message_id=str(gmail_draft.get("message_id") or "") or None,
            )
        except GmailUnavailable as exc:
            log.warning("resolve_sent_body failed for thread %s: %s", thread_id, exc)
        edit_payload = None
        if agent_body and sent_body:
            edit_payload = reply_diff.build_edit_learning_payload(
                agent_body=agent_body,
                sent_body=sent_body,
                child_skill=child_skill,
                goal=goal,
                sent_message_id=sent_message_id,
            )
        event_id = cal.write_event(
            identity_id=identity_id,
            campaign_id=campaign_id,
            event_type="outbound_sent",
            goal=goal or "outreach",
            lane=lane or "commerce",
            actor="gmail:sent-reconcile",
            payload={
                "thread_id": thread_id,
                "gmail_draft": gmail_draft,
                "edit_learning": edit_payload,
            },
            env=env,
        )
        if edit_payload is not None:
            cal.write_event(
                identity_id=identity_id,
                campaign_id=campaign_id,
                event_type="draft_edit_learning",
                goal=goal or None,
                lane=lane or None,
                actor="gmail:sent-reconcile",
                payload=edit_payload,
                env=env,
            )
            edit_learning_count += 1
        cal.write_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespace="offer",
            facts={
                "offer.outreach_sent": True,
                "offer.outreach_sent_at": now,
                "offer.gmail_sent_thread_id": thread_id,
            },
            source="gmail:sent-reconcile",
            source_event_id=event_id,
            env=env,
        )
        reconciled.append({
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "thread_id": thread_id,
            "event_id": event_id,
            "was_edited": bool(edit_payload and edit_payload.get("was_edited")),
        })
    return {
        "env": env,
        "sent_threads_seen": len(sent_thread_ids),
        "reconciled_count": len(reconciled),
        "edit_learning_count": edit_learning_count,
        "reconciled": reconciled,
    }
