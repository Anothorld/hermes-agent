"""Discard orphan Gmail drafts when chase supersedes a prior reply approval."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from . import cal
from . import mailbox_resolver
from .gmail_client import GmailClient, GmailUnavailable
from .gmail_credentials import client_for_user
from .reply_draft import extract_thread_anchors, is_cold_outreach_anchor

log = logging.getLogger(__name__)


def _prior_is_outreach_approval(prior_fact: Mapping[str, Any]) -> bool:
    """True when supersede replaces the initial-outreach approval, not a reply draft."""
    source = str(prior_fact.get("source_message_id") or "").strip()
    if is_cold_outreach_anchor(source):
        return True
    thread_id, anchor_source = extract_thread_anchors(prior_fact)
    return is_cold_outreach_anchor(thread_id) or is_cold_outreach_anchor(anchor_source)


def _prior_is_approved_unsent_reply_draft(prior_fact: Mapping[str, Any]) -> bool:
    """True for chase targets: approved reply draft with a Gmail draftId, not outreach."""
    if prior_fact.get("decision") != "approved":
        return False
    if not isinstance(prior_fact.get("gmail_draft"), dict):
        return False
    if _prior_is_outreach_approval(prior_fact):
        return False
    gmail_draft = prior_fact.get("gmail_draft")
    assert isinstance(gmail_draft, dict)
    return bool(str(gmail_draft.get("draft_id") or "").strip())


def _clear_stale_draft_facts(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    draft_id: str,
    offer_facts: Mapping[str, Any],
    clear_thread_id: bool,
) -> bool:
    """Clear ``offer.gmail_draft_id`` when it still points at ``draft_id``."""
    if str(offer_facts.get("offer.gmail_draft_id") or "").strip() != draft_id:
        return False
    stale_offer: dict[str, Any] = {"offer.gmail_draft_id": ""}
    if clear_thread_id:
        stale_offer["offer.gmail_thread_id"] = ""
    cal.write_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        namespace="offer",
        facts=stale_offer,
        source="gmail:orphan-draft-discard",
        env=env,
    )
    return True


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
    """Delete a superseded **approved-but-unsent reply** Gmail draft.

    Chase supersede that replaces the initial-outreach ``approval.reply_draft``
    (``draft:outreach_*`` anchors) only clears stale CAL facts — it never calls
    Gmail ``delete-draft``. Best-effort: failures are logged but never block
    supersede.
    """
    offer_facts = cal.latest_facts_for(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    draft_id = orphan_draft_id(prior_fact, offer_facts=offer_facts)
    if not draft_id:
        return {"action": "skipped", "reason": "no_orphan_draft_id"}

    if _prior_is_outreach_approval(prior_fact):
        facts_cleared = _clear_stale_draft_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            draft_id=draft_id,
            offer_facts=offer_facts,
            clear_thread_id=False,
        )
        log.info(
            "orphan draft discard skipped (superseded outreach approval) "
            "identity=%s campaign=%s draft_id=%s",
            identity_id,
            campaign_id,
            draft_id,
        )
        return {
            "action": "skipped",
            "reason": "superseded_outreach_approval",
            "draft_id": draft_id,
            "facts_cleared": facts_cleared,
        }

    if not _prior_is_approved_unsent_reply_draft(prior_fact):
        return {
            "action": "skipped",
            "reason": "not_approved_unsent_reply_draft",
            "draft_id": draft_id,
        }

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

    _clear_stale_draft_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        draft_id=draft_id,
        offer_facts=offer_facts,
        clear_thread_id=True,
    )

    return {"action": "deleted", "draft_id": draft_id}


__all__ = [
    "discard_orphan_gmail_draft",
    "orphan_draft_id",
    "resolve_campaign_gmail_client",
]
