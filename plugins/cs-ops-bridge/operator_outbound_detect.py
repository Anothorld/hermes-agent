"""Detect whether QuickCEP message history shows a manual operator email reply."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .rating_inbound import is_customer_rating_content_type

_SKIP_OWNER_TYPES = frozenset({"system", "bot", "botSystem"})


def pick_latest_operator_outbound_email(
    messages: list[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """Return the newest operator/html outbound when it is the latest conversational message.

    Messages are expected in QuickCEP default order (createTime DESC — newest first).
    Internal notes, system events, bot messages, and customer rating / survey
    rows are skipped when scanning for the latest conversational turn; the
    first non-skipped row must be operator/html. A visitor-owned rating row
    (``score_notify`` submitted by the customer) is a non-conversational stop
    so a CSAT on top of an already-synced operator reply does not re-sync the
    stale reply. A system-owned rating (e.g. the ``invite_score`` CSAT invite)
    is skipped via ``_SKIP_OWNER_TYPES`` so the operator/html below it — which
    IS the latest conversational turn — is still found and synced (the CSAT
    invite is post-resolution system telemetry, not a customer turn that
    should block operator_sent handoff).
    """
    if not messages:
        return None
    for msg in messages:
        owner = str(msg.get("ownerType") or "")
        ctype = str(msg.get("contentType") or "")
        if owner in _SKIP_OWNER_TYPES:
            continue
        if owner == "operatorNote" or ctype == "internalNote":
            continue
        if is_customer_rating_content_type(ctype):
            return None
        if owner == "operator" and ctype == "html":
            msg_id = str(msg.get("id") or "").strip()
            if not msg_id:
                return None
            return {
                "id": msg_id,
                "createTime": msg.get("createTime") or msg.get("time") or "",
            }
        return None
    return None
