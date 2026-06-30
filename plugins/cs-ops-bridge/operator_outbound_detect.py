"""Detect whether QuickCEP message history shows a manual operator email reply."""

from __future__ import annotations

from typing import Any, Mapping, Optional

_SKIP_OWNER_TYPES = frozenset({"system", "bot", "botSystem"})


def pick_latest_operator_outbound_email(
    messages: list[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """Return the newest operator/html outbound when it is the latest conversational message.

    Messages are expected in QuickCEP default order (createTime DESC — newest first).
    Internal notes, system events, and bot messages are skipped when scanning for the
    latest conversational turn; the first non-skipped row must be operator/html.
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
