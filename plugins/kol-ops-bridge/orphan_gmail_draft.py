"""Discard orphan Gmail drafts when chase supersedes a prior reply approval."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from . import cal
from . import mailbox_resolver
from .gmail_client import GmailClient, GmailUnavailable
from .gmail_credentials import client_for_user

log = logging.getLogger(__name__)


def orphan_draft_id(
    prior_fact: Mapping[str, Any],
    *,
    offer_facts: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the Gmail ``draftId`` to delete for a superseded reply draft."""
    draft_id = ""
    gmail_draft = prior_fact.get("gmail_draft")
    if isinstance(gmail_draft, dict):
        draft_id = str(gmail_draft.get("draft_id") or "").strip()
    if not draft_id and offer_facts:
        draft_id = str(offer_facts.get("offer.gmail_draft_id") or "").strip()
    return draft_id or None


def resolve_campaign_gmail_client(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
) -> GmailClient | None:
    """Best-effort Gmail client for the campaign mailbox (binding → legacy token)."""
    binding = mailbox_resolver.read_binding(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if binding is not None:
        client = client_for_user(binding.user_id)
        if client.is_available():
            return client
        log.warning(
            "orphan draft discard: bound mailbox user_id=%s unavailable identity=%s campaign=%s",
            binding.user_id,
            identity_id,
            campaign_id,
        )
        return None
    client = GmailClient()
    if client.is_available():
        return client
    return None


def discard_orphan_gmail_draft(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    prior_fact: Mapping[str, Any],
    client: Optional[GmailClient] = None,
) -> dict[str, Any]:
    """Delete a superseded Gmail draft and clear stale ``offer.gmail_*`` facts.

    Best-effort: reconcile failures are logged but never block chase supersede.
    """
    offer_facts = cal.latest_facts_for(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    draft_id = orphan_draft_id(prior_fact, offer_facts=offer_facts)
    if not draft_id:
        return {"action": "skipped", "reason": "no_orphan_draft_id"}

    gmail = client or resolve_campaign_gmail_client(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if gmail is None:
        return {
            "action": "skipped",
            "reason": "gmail_unavailable",
            "draft_id": draft_id,
        }

    try:
        gmail.delete_draft(draft_id=draft_id)
    except GmailUnavailable as exc:
        log.warning(
            "orphan draft discard failed identity=%s campaign=%s draft_id=%s: %s",
            identity_id,
            campaign_id,
            draft_id,
            exc,
        )
        return {
            "action": "failed",
            "reason": str(exc),
            "draft_id": draft_id,
        }

    stale_offer: dict[str, Any] = {}
    if str(offer_facts.get("offer.gmail_draft_id") or "").strip() == draft_id:
        stale_offer["offer.gmail_draft_id"] = ""
        stale_offer["offer.gmail_thread_id"] = ""
    if stale_offer:
        cal.write_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespace="offer",
            facts=stale_offer,
            source="gmail:orphan-draft-discard",
            env=env,
        )

    return {"action": "deleted", "draft_id": draft_id}


__all__ = [
    "discard_orphan_gmail_draft",
    "orphan_draft_id",
    "resolve_campaign_gmail_client",
]
