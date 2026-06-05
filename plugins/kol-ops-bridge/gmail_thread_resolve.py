"""Resolve Gmail thread ids safe for ``drafts.create``.

Upstream drafting skills sometimes place a ``message_id`` (or a synthetic
``proactive-followup:…`` token) where Gmail expects a ``threadId``. The API
then returns ``400 Invalid thread id value``. This module verifies anchors
against the operator mailbox before approve-time draft creation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gmail_client import GmailClient

_GMAIL_RESOURCE_ID = re.compile(r"^[0-9a-fA-F]{10,}$")


def is_plausible_gmail_resource_id(value: str | None) -> bool:
    """True when *value* looks like a Gmail message/thread id (hex), not synthetic."""
    if not value or not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or ":" in stripped:
        return False
    return bool(_GMAIL_RESOURCE_ID.fullmatch(stripped))


def _dedupe_candidates(*values: str | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        candidate = (raw or "").strip()
        if not candidate or not is_plausible_gmail_resource_id(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def resolve_thread_id_for_draft(
    client: GmailClient,
    *,
    candidate_thread_id: str | None,
    source_message_id: str | None,
) -> str | None:
    """Return a Gmail ``threadId`` that ``drafts.create`` accepts, or ``None``.

    Resolution order for each plausible hex id (thread candidate, then source
    message id):

    1. ``get_thread(id)`` — id is already a valid thread.
    2. ``get_message(id).thread_id`` — id was a message id stored as thread id.
    """
    from .gmail_client import GmailUnavailable

    for resource_id in _dedupe_candidates(candidate_thread_id, source_message_id):
        if client.get_thread(resource_id):
            return resource_id
        try:
            msg = client.get_message(resource_id)
        except GmailUnavailable:
            continue
        thread_id = (msg.thread_id or "").strip()
        if thread_id and client.get_thread(thread_id):
            return thread_id
    return None


__all__ = [
    "is_plausible_gmail_resource_id",
    "resolve_thread_id_for_draft",
]
