"""Classify ``approval.reply_draft`` as initial outreach vs inbound reply."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_INITIAL_OUTREACH_CHILD_SKILLS = frozenset({
    "kol-cold-outreach",
    "kol-reengagement-outreach",
})


def is_initial_outreach_reply_draft(
    reply_draft: Mapping[str, Any],
    *,
    campaign_id: str,
    identity_id: int,
) -> bool:
    """True when ``approval.reply_draft`` is the first-touch outreach envelope."""
    child_skill = str(reply_draft.get("child_skill") or "").strip()
    if child_skill in _INITIAL_OUTREACH_CHILD_SKILLS:
        return True
    draft = reply_draft.get("draft")
    if isinstance(draft, dict) and draft.get("kind") == "initial_outreach":
        return True
    expected_thread = f"outreach_{campaign_id}_{identity_id}"
    expected_source = f"draft:outreach_{campaign_id}_{identity_id}"
    for key in ("source_message_id", "thread_id"):
        anchor = str(reply_draft.get(key) or "").strip()
        if not anchor:
            continue
        if anchor.startswith("draft:outreach_") or anchor.startswith("outreach_"):
            return True
        if anchor in (expected_thread, expected_source):
            return True
    return False
