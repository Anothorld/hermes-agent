"""Gmail SENT reconciliation — mark approved drafts sent + edit-learning capture."""

from __future__ import annotations

import fcntl
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import cal
from . import mailbox_resolver
from . import reply_diff
from .gmail_client import GmailClient, GmailUnavailable
from .gmail_console import list_operator_gmail_clients

log = logging.getLogger(__name__)

_GMAIL_LOCK_PATH = Path(
    os.environ.get(
        "KOL_OPS_GMAIL_RECONCILE_LOCK",
        str(Path.home() / ".hermes" / "kol-ops-bridge" / "gmail_reconcile.lock"),
    )
)


@contextmanager
def _gmail_reconcile_lock() -> Iterator[None]:
    """Cross-process lock so poller + reply_watcher do not overlap SENT scans."""
    _GMAIL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _GMAIL_LOCK_PATH.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


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


def has_draft_edit_learning_event(
    conn: sqlite3.Connection,
    *,
    env: str,
    identity_id: int,
    campaign_id: Optional[str],
) -> bool:
    """True when a ``draft_edit_learning`` row already exists for the pair."""
    if campaign_id:
        row = conn.execute(
            """SELECT 1 FROM kol_conversation_events
                WHERE env=? AND identity_id=? AND campaign_id=?
                  AND event_type='draft_edit_learning'
                LIMIT 1""",
            (env, int(identity_id), campaign_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT 1 FROM kol_conversation_events
                WHERE env=? AND identity_id=? AND campaign_id IS NULL
                  AND event_type='draft_edit_learning'
                LIMIT 1""",
            (env, int(identity_id)),
        ).fetchone()
    return row is not None


def _build_edit_payload(
    *,
    gmail: GmailClient,
    row: dict[str, Any],
    thread_id: str,
    gmail_draft: dict[str, Any],
    operator_user_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    approval_value = row.get("value") if isinstance(row.get("value"), dict) else {}
    draft_obj = approval_value.get("draft") if isinstance(approval_value.get("draft"), dict) else {}
    agent_body = str(draft_obj.get("body") or "")
    goal = str(approval_value.get("primary_goal") or "outreach")
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
    if not agent_body or not sent_body:
        return None
    return reply_diff.build_edit_learning_payload(
        agent_body=agent_body,
        sent_body=sent_body,
        child_skill=child_skill,
        goal=goal,
        sent_message_id=sent_message_id,
        operator_user_id=operator_user_id,
    )


def _process_sent_reply_row(
    *,
    gmail: GmailClient,
    row: dict[str, Any],
    env: str,
    now: str,
    mailbox_user_id: Optional[int] = None,
    sent_thread_ids: Optional[set[str]] = None,
    write_outbound_sent: bool = True,
    skip_if_edit_exists: bool = False,
) -> dict[str, Any]:
    """Reconcile one approved reply draft (sent capture and/or edit-learning)."""
    gmail_draft = row.get("gmail_draft") if isinstance(row, dict) else {}
    thread_id = gmail_draft.get("thread_id") if isinstance(gmail_draft, dict) else None
    if not thread_id:
        return {"skipped": True, "reason": "missing_thread_id"}
    if sent_thread_ids is not None and thread_id not in sent_thread_ids:
        return {"skipped": True, "reason": "not_in_sent_threads", "thread_id": thread_id}

    identity_id = int(row["identity_id"])
    campaign_id = row.get("campaign_id")
    if not _draft_owned_by_mailbox(
        identity_id=identity_id,
        campaign_id=str(campaign_id) if campaign_id else None,
        env=env,
        mailbox_user_id=mailbox_user_id,
    ):
        return {"skipped": True, "reason": "mailbox_mismatch", "thread_id": thread_id}

    approval_value = row.get("value") if isinstance(row.get("value"), dict) else {}
    goal = str(approval_value.get("primary_goal") or "outreach")
    lane = str(approval_value.get("primary_lane") or "commerce")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        if skip_if_edit_exists and has_draft_edit_learning_event(
            conn, env=env, identity_id=identity_id, campaign_id=campaign_id,
        ):
            return {"skipped": True, "reason": "edit_learning_exists", "thread_id": thread_id}

    edit_payload = _build_edit_payload(
        gmail=gmail, row=row, thread_id=str(thread_id), gmail_draft=gmail_draft,
        operator_user_id=mailbox_user_id,
    )

    event_id: Optional[int] = None
    edit_event_written = False
    if write_outbound_sent:
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
    elif edit_payload is not None:
        cal.write_event(
            identity_id=identity_id,
            campaign_id=campaign_id,
            event_type="draft_edit_learning",
            goal=goal or None,
            lane=lane or None,
            actor="gmail:edit-learning-backfill",
            payload=edit_payload,
            env=env,
        )
        edit_event_written = True

    if write_outbound_sent and edit_payload is not None:
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
        edit_event_written = True

    if (
        write_outbound_sent
        and campaign_id
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

    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "thread_id": thread_id,
        "event_id": event_id,
        "edit_learning_written": edit_event_written,
        "was_edited": bool(edit_payload and edit_payload.get("was_edited")),
        "edit_distance": (edit_payload or {}).get("edit_distance"),
        "skip_reason": None if edit_payload else "no_agent_or_sent_body",
    }


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
    with _gmail_reconcile_lock():
        return _run_reconcile_sent_unlocked(
            env=env,
            lookback_days=lookback_days,
            max_results=max_results,
            client=client,
            mailbox_user_id=mailbox_user_id,
        )


def _run_reconcile_sent_unlocked(
    *,
    env: str,
    lookback_days: int = 7,
    max_results: int = 100,
    client: Optional[GmailClient] = None,
    mailbox_user_id: Optional[int] = None,
) -> dict[str, Any]:
    gmail = client or GmailClient()
    if not gmail.is_available():
        raise GmailUnavailable("gmail token or google_api.py unavailable")

    sent_thread_ids = set(
        gmail.list_sent_thread_ids(
            lookback_days=int(lookback_days),
            max_results=int(max_results),
        ),
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reconciled: list[dict[str, Any]] = []
    edit_learning_count = 0
    for row in cal.list_approved_reply_drafts(env=env):
        outcome = _process_sent_reply_row(
            gmail=gmail,
            row=row,
            env=env,
            now=now,
            mailbox_user_id=mailbox_user_id,
            sent_thread_ids=sent_thread_ids,
            write_outbound_sent=True,
            skip_if_edit_exists=False,
        )
        if outcome.get("skipped"):
            continue
        if outcome.get("edit_learning_written"):
            edit_learning_count += 1
        reconciled.append(outcome)
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
    with _gmail_reconcile_lock():
        return _run_reconcile_all_mailboxes_unlocked(
            env=env,
            lookback_days=lookback_days,
            max_results=max_results,
        )


def _run_reconcile_all_mailboxes_unlocked(
    *,
    env: str,
    lookback_days: int = 7,
    max_results: int = 100,
) -> dict[str, Any]:
    mailboxes = list_operator_gmail_clients()
    if not mailboxes:
        raise GmailUnavailable("no operator Gmail connections available")
    per_mailbox: list[dict[str, Any]] = []
    total_reconciled = 0
    total_edit_learning = 0
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
        total_edit_learning += int(summary.get("edit_learning_count") or 0)
    return {
        "env": env,
        "mailbox_count": len(per_mailbox),
        "reconciled_count": total_reconciled,
        "edit_learning_count": total_edit_learning,
        "mailboxes": per_mailbox,
    }


def backfill_edit_learning(
    *,
    env: str,
    dry_run: bool = False,
    limit: int = 500,
    client: Optional[GmailClient] = None,
    mailbox_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Backfill ``draft_edit_learning`` for already-sent approved reply drafts."""
    gmail = client or GmailClient()
    if not dry_run and not gmail.is_available():
        raise GmailUnavailable("gmail token or google_api.py unavailable")

    candidates = cal.list_sent_reply_drafts_for_edit_learning(env=env)[: max(1, int(limit))]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    processed: list[dict[str, Any]] = []
    skipped = 0
    edit_learning_count = 0
    edited_count = 0

    for row in candidates:
        if dry_run:
            with cal._connect() as conn:  # type: ignore[attr-defined]
                if has_draft_edit_learning_event(
                    conn,
                    env=env,
                    identity_id=int(row["identity_id"]),
                    campaign_id=row.get("campaign_id"),
                ):
                    skipped += 1
                    continue
            processed.append({
                "identity_id": row.get("identity_id"),
                "campaign_id": row.get("campaign_id"),
                "dry_run": True,
            })
            continue

        outcome = _process_sent_reply_row(
            gmail=gmail,
            row=row,
            env=env,
            now=now,
            mailbox_user_id=mailbox_user_id,
            sent_thread_ids=None,
            write_outbound_sent=False,
            skip_if_edit_exists=True,
        )
        if outcome.get("skipped") or not outcome.get("edit_learning_written"):
            skipped += 1
            continue
        edit_learning_count += 1
        if outcome.get("was_edited"):
            edited_count += 1
        processed.append(outcome)

    return {
        "env": env,
        "dry_run": dry_run,
        "candidates_seen": len(candidates),
        "backfilled_count": len(processed),
        "edit_learning_count": edit_learning_count,
        "edited_was_edited_count": edited_count,
        "skipped_count": skipped,
        "processed": processed,
    }


def backfill_edit_learning_all_mailboxes(
    *,
    env: str,
    dry_run: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """Run edit-learning backfill once per connected operator mailbox."""
    if dry_run:
        return backfill_edit_learning(env=env, dry_run=True, limit=limit)

    mailboxes = list_operator_gmail_clients()
    if not mailboxes:
        raise GmailUnavailable("no operator Gmail connections available")
    per_mailbox: list[dict[str, Any]] = []
    total_backfilled = 0
    total_edit_learning = 0
    total_skipped = 0
    for mb in mailboxes:
        summary = backfill_edit_learning(
            env=env,
            dry_run=False,
            limit=limit,
            client=mb.client,
            mailbox_user_id=mb.user_id,
        )
        summary["mailbox_user_id"] = mb.user_id
        summary["mailbox_email"] = mb.google_email
        per_mailbox.append(summary)
        total_backfilled += int(summary.get("backfilled_count") or 0)
        total_edit_learning += int(summary.get("edit_learning_count") or 0)
        total_skipped += int(summary.get("skipped_count") or 0)
    return {
        "env": env,
        "mailbox_count": len(per_mailbox),
        "backfilled_count": total_backfilled,
        "edit_learning_count": total_edit_learning,
        "skipped_count": total_skipped,
        "mailboxes": per_mailbox,
    }
