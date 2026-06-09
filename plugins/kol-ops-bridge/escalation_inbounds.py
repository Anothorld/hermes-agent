"""Pending inbound anchors on open escalations (trigger + follow-ups)."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def inbound_anchor_from_payload(
    payload: Mapping[str, Any],
    *,
    event_id: Optional[int] = None,
    ts: Optional[str] = None,
    role: str = "followup",
) -> dict[str, Any]:
    """Shape one ``resume_context.pending_inbounds[]`` entry."""
    msg_id = payload.get("message_id")
    if not isinstance(msg_id, str) or not msg_id.strip():
        return {}
    snippet = payload.get("snippet") or payload.get("body") or ""
    if isinstance(snippet, str) and len(snippet) > 280:
        snippet = snippet[:277] + "..."
    return {
        "message_id": msg_id.strip(),
        "thread_id": (
            payload.get("thread_id").strip()
            if isinstance(payload.get("thread_id"), str)
            and payload.get("thread_id").strip()
            else None
        ),
        "event_id": event_id,
        "ts": ts,
        "role": role,
        "from_addr": payload.get("from_addr"),
        "subject": payload.get("subject"),
        "snippet": snippet if isinstance(snippet, str) else None,
    }


def seed_trigger_inbound(ctx: dict[str, Any]) -> dict[str, Any]:
    """Ensure ``pending_inbounds`` includes the trigger ``source_message_id``."""
    out = dict(ctx)
    msg_id = out.get("source_message_id")
    if not isinstance(msg_id, str) or not msg_id.strip():
        return out
    msg_id = msg_id.strip()
    pending = out.get("pending_inbounds")
    if not isinstance(pending, list):
        pending = []
    else:
        pending = [x for x in pending if isinstance(x, dict)]
    if not any(str(x.get("message_id") or "") == msg_id for x in pending):
        pending.append({
            "message_id": msg_id,
            "thread_id": (
                out.get("thread_id").strip()
                if isinstance(out.get("thread_id"), str)
                and out.get("thread_id").strip()
                else None
            ),
            "role": "trigger",
        })
    out["pending_inbounds"] = pending
    out["latest_pending_inbound_message_id"] = _latest_message_id(pending)
    return out


def append_pending_inbound(
    ctx: dict[str, Any],
    anchor: Mapping[str, Any],
) -> dict[str, Any]:
    """Append a follow-up inbound anchor; no-op when ``message_id`` duplicate."""
    if not anchor or not anchor.get("message_id"):
        return ctx
    out = dict(ctx)
    pending = out.get("pending_inbounds")
    if not isinstance(pending, list):
        pending = []
    else:
        pending = [dict(x) for x in pending if isinstance(x, dict)]
    msg_id = str(anchor.get("message_id"))
    if any(str(x.get("message_id") or "") == msg_id for x in pending):
        return out
    entry = dict(anchor)
    entry.setdefault("role", "followup")
    pending.append(entry)
    out["pending_inbounds"] = pending
    out["latest_pending_inbound_message_id"] = msg_id
    return out


def _latest_message_id(pending: list[dict[str, Any]]) -> Optional[str]:
    for row in reversed(pending):
        mid = row.get("message_id")
        if isinstance(mid, str) and mid.strip():
            return mid.strip()
    return None


_MAX_SUGGESTED_QUESTION_CHARS = 4000
_FOLLOWUP_MARKER = "【KOL 追信"


def format_followup_question_block(anchor: Mapping[str, Any]) -> str:
    """Operator-facing summary block for one follow-up inbound."""
    msg_id = anchor.get("message_id")
    if not isinstance(msg_id, str) or not msg_id.strip():
        return ""
    subject = anchor.get("subject")
    snippet = anchor.get("snippet") or ""
    from_addr = anchor.get("from_addr")
    lines = [f"{_FOLLOWUP_MARKER} · {msg_id.strip()}】"]
    if isinstance(from_addr, str) and from_addr.strip():
        lines.append(f"发件人：{from_addr.strip()}")
    if isinstance(subject, str) and subject.strip():
        lines.append(f"主题：{subject.strip()}")
    if isinstance(snippet, str) and snippet.strip():
        text = snippet.strip()
        if len(text) > 320:
            text = text[:317] + "..."
        lines.append(text)
    if len(lines) <= 1:
        lines.append("（无正文摘要，请在下方「待处理回信」查看全文）")
    return "\n".join(lines)


def append_followup_to_suggested_question(
    existing: Optional[str],
    anchor: Mapping[str, Any],
) -> str:
    """Append latest follow-up summary; idempotent per ``message_id``."""
    msg_id = anchor.get("message_id")
    if not isinstance(msg_id, str) or not msg_id.strip():
        return (existing or "").strip()
    marker = f"{_FOLLOWUP_MARKER} · {msg_id.strip()}】"
    base = (existing or "").strip()
    if marker in base:
        return base
    block = format_followup_question_block(anchor)
    if not block:
        return base
    merged = f"{base}\n\n---\n{block}" if base else block
    if len(merged) <= _MAX_SUGGESTED_QUESTION_CHARS:
        return merged
    room = _MAX_SUGGESTED_QUESTION_CHARS - len(base) - len("\n\n---\n")
    if room < 80:
        return base[:_MAX_SUGGESTED_QUESTION_CHARS]
    trimmed = block[: max(0, room - 3)] + "..."
    return f"{base}\n\n---\n{trimmed}" if base else trimmed
