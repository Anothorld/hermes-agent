"""Customer rating / survey message detection for inbound skip.

QuickCEP emits two non-conversational system message types after a case is
resolved: ``invite_score`` (the survey invite) and ``score_notify`` (the
submitted score + feedback, e.g. "客户评分 1★ / Terrible communication").
Console renders both as system rows; the bridge must NOT launch AI on them,
must NOT relabel the CAL session, and must NOT trigger leave-chat.

This module is a thin shared detector used by:

- ``quickcep_watcher._launch_for_message`` (message-level consume gate)
- ``quickcep_watcher.run_rest_reconcile_once`` (REST pre-filter for rows with
  no CAL session, to avoid a 60s re-poll loop)
- ``operator_outbound_detect.pick_latest_operator_outbound_email`` (treat
  rating rows as non-conversational so a rating on top does not hide or
  re-sync a stale operator reply)
"""

from __future__ import annotations

from typing import Any, Mapping

# QuickCEP contentType values that represent customer CSAT / survey events.
# Case-insensitive — compared via ``is_customer_rating_content_type``.
CUSTOMER_RATING_CONTENT_TYPES = frozenset({"invite_score", "score_notify"})


def is_customer_rating_content_type(value: Any) -> bool:
    """True when ``value`` is a QuickCEP rating/survey contentType."""
    return str(value or "").strip().lower() in CUSTOMER_RATING_CONTENT_TYPES


def is_customer_rating_inbound(info: Mapping[str, Any]) -> bool:
    """True when an inbound payload (SIO or REST-derived ``info``) is a rating.

    Detects on either ``contentType`` (SIO native message field) or
    ``lastMsgContentType`` (REST session-list row field). Both are checked
    case-insensitively. No free-text / regex matching on "客户评分" — we rely
    on QuickCEP's structured contentType, mirroring Console ``mappers.js``.
    """
    if not isinstance(info, Mapping):
        return False
    if is_customer_rating_content_type(info.get("contentType")):
        return True
    if is_customer_rating_content_type(info.get("lastMsgContentType")):
        return True
    return False
