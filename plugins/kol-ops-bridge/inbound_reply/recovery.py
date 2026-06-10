"""Recovery probes for globally-seen inbound messages."""

from __future__ import annotations

from ..gmail_client import GmailMessage
from .deps import BridgeRequestError, InboundBridgePort, MatchBridgeError
from .matcher import match_identity


def needs_reprocess_after_global_seen(
    msg: GmailMessage,
    *,
    env: str,
    bridge: InboundBridgePort,
) -> bool:
    """Return True when a globally-seen message still needs poller attention."""
    try:
        matched = match_identity(msg, env=env, bridge=bridge)
    except MatchBridgeError:
        return True
    if not matched or not matched.campaign_id:
        return False
    try:
        dispatch_status = bridge.reply_dispatch_status(
            identity_id=matched.identity_id,
            campaign_id=str(matched.campaign_id),
            message_id=msg.message_id,
            env=env,
        )
    except BridgeRequestError:
        return True
    if not isinstance(dispatch_status, dict):
        return False
    if dispatch_status.get("should_retry_gateway_only"):
        return True
    if dispatch_status.get("should_skip_poller"):
        if (
            dispatch_status.get("has_mailbox_mismatch_escalation")
            and not dispatch_status.get("has_draft_ready_event")
            and not dispatch_status.get("has_pending_reply_draft")
        ):
            return True
        return False
    return False
