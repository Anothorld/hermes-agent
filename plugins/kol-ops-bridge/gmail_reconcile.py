"""Gmail SENT reconciliation — mark approved drafts sent + edit-learning capture."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import cal
from . import mailbox_resolver
from . import reply_diff
from .gmail_client import GmailClient, GmailUnavailable
from .gmail_console import list_operator_gmail_clients

log = logging.getLogger(__name__)


def _draft_owned_by_mailbox(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    mailbox_user_id: Optional[int],
) -> bool:
    """True when this draft should be reconciled against the given mailbox."""
    if mailbox_user_id is None:
        return True
    if not campaign_id:
        return mailbox_user_id == 0
    binding = mailbox_resolver.read_binding(
        identity_id=identity_id,
        campaign_id=str(campaign_id),
        env=env,
    )
    if binding is None:
        return mailbox_user_id == 0
    return binding.user_id == mailbox_user_id


def run_reconcile_sent(
    *,
    env: str,
    lookback_days: int = 7,
    max_results: int = 100,
    client: Optional[GmailClient] = None,
    mailbox_user_id: Optional[int] = None,
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
        if not _draft_owned_by_mailbox(
            identity_id=identity_id,
            campaign_id=str(campaign_id) if campaign_id else None,
            env=env,
            mailbox_user_id=mailbox_user_id,
        ):
            continue
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
        if (
            campaign_id
            and mailbox_user_id is not None
            and mailbox_user_id > 0
            and mailbox_resolver.read_binding(
                identity_id=identity_id,
                campaign_id=str(campaign_id),
                env=env,
            )
            is None
        ):
            profile_email = ""
            try:
                profile_email = gmail.get_profile_email() or ""
            except GmailUnavailable:
                pass
            try:
                mailbox_resolver.bind_mailbox(
                    identity_id=identity_id,
                    campaign_id=str(campaign_id),
                    env=env,
                    operator_user_id=int(mailbox_user_id),
                    operator_email=profile_email,
                    source="gmail:sent-reconcile",
                )
            except mailbox_resolver.MailboxError as exc:
                log.warning(
                    "sent-reconcile bind skipped identity=%s campaign=%s: %s",
                    identity_id,
                    campaign_id,
                    exc,
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


def run_reconcile_all_mailboxes(
    *,
    env: str,
    lookback_days: int = 7,
    max_results: int = 100,
) -> dict[str, Any]:
    """Run sent reconciliation once per connected operator mailbox."""
    mailboxes = list_operator_gmail_clients()
    if not mailboxes:
        raise GmailUnavailable("no operator Gmail connections available")
    per_mailbox: list[dict[str, Any]] = []
    total_reconciled = 0
    for mb in mailboxes:
        summary = run_reconcile_sent(
            env=env,
            lookback_days=lookback_days,
            max_results=max_results,
            client=mb.client,
            mailbox_user_id=mb.user_id,
        )
        summary["mailbox_user_id"] = mb.user_id
        summary["mailbox_email"] = mb.google_email
        per_mailbox.append(summary)
        total_reconciled += int(summary.get("reconciled_count") or 0)
    return {
        "env": env,
        "mailbox_count": len(per_mailbox),
        "reconciled_count": total_reconciled,
        "mailboxes": per_mailbox,
    }
