"""Detect QuickCEP session close in message history (live chat + email)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_CLOSE_MARKERS = ('"action":"chat_end"', '"action":"leaveChat"')


def message_content_indicates_closed(content: str) -> bool:
    """True when QuickCEP recorded operator leave / chat_end."""
    text = content or ""
    return any(marker in text for marker in _CLOSE_MARKERS)


def messages_payload_indicates_closed(payload: dict[str, Any]) -> bool:
    for msg in payload.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, dict):
            action = str(content.get("action") or "")
            if action in {"chat_end", "leaveChat"}:
                return True
        elif message_content_indicates_closed(str(content or "")):
            return True
    return False


def confirm_closed_via_messages_cli(*, cli: Path, session_id: str, page_size: int = 15) -> bool:
    """Fallback when leave-chat API succeeds but legacy CLI only checks chat_end."""
    if not cli.is_file():
        return False
    proc = subprocess.run(
        [sys.executable, str(cli), "messages", session_id, "--page-size", str(page_size)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return False
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False
    return messages_payload_indicates_closed(payload)


def reconcile_leave_chat_payload(
    payload: dict[str, Any],
    *,
    cli: Path,
    session_id: str,
) -> dict[str, Any]:
    """Upgrade leave-chat result when email channel emitted leaveChat instead of chat_end."""
    if payload.get("ok"):
        return payload
    err = str(payload.get("error") or "")
    if payload.get("result_code") != 200 or err != "chat_end_not_confirmed":
        return payload
    if not confirm_closed_via_messages_cli(cli=cli, session_id=session_id):
        return payload
    merged = dict(payload)
    merged["ok"] = True
    merged["chat_end"] = True
    merged["confirmed_via"] = "leaveChat_message"
    return merged
