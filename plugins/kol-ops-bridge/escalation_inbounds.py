"""Pending inbound anchors on open escalations (trigger + follow-ups)."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


def is_inbound_tagged_resume_context(ctx: Mapping[str, Any] | None) -> bool:
    """True when an escalation is tied to an inbound KOL reply thread."""
    if not isinstance(ctx, Mapping):
        return False
    source = ctx.get("source")
    if source in ("classifier", "dispatcher"):
        return True
    if ctx.get("source_message_id"):
        return True
    pending = ctx.get("pending_inbounds")
    return isinstance(pending, list) and len(pending) > 0


def needs_pending_inbound_sync(
    ctx: Mapping[str, Any],
    inbound_events: Sequence[Mapping[str, Any]],
    *,
    escalation_created_at: str,
    parse_payload: Any = None,
) -> bool:
    """True when timeline has inbounds not yet listed on the escalation."""
    pending = ctx.get("pending_inbounds")
    known: set[str] = set()
    if isinstance(pending, list):
        for row in pending:
            if isinstance(row, dict):
                mid = row.get("message_id")
                if isinstance(mid, str) and mid.strip():
                    known.add(mid.strip())
    _ = escalation_created_at  # reserved for future ordering hints
    for ev in inbound_events:
        if not isinstance(ev, dict) or ev.get("event_type") != "kol_inbound_reply":
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        mid = payload.get("message_id")
        if isinstance(mid, str) and mid.strip() and mid.strip() not in known:
            return True
    return len(known) == 0 and bool(inbound_events)


def select_escalation_ids_for_followup(
    rows: Sequence[Mapping[str, Any]],
    anchor: Mapping[str, Any],
    *,
    parse_ctx: Any,
) -> list[int]:
    """Pick the single best ``awaiting_answer`` row for a follow-up inbound.

    Prefer inbound-tagged escalations on the same Gmail ``thread_id``; fall
    back to the newest inbound-tagged row. Internal-only escalations are
    skipped when any inbound-tagged row exists.
    """
    if not rows:
        return []
    def _row_value(row: Mapping[str, Any], key: str) -> Any:
        if hasattr(row, "get"):
            return row.get(key)
        try:
            return row[key]  # type: ignore[index]
        except (KeyError, TypeError, IndexError):
            return None

    def _row_ctx(row: Mapping[str, Any]) -> dict[str, Any]:
        return parse_ctx(_row_value(row, "resume_context_json")) or {}

    inbound_rows: list[Mapping[str, Any]] = []
    for row in rows:
        if is_inbound_tagged_resume_context(_row_ctx(row)):
            inbound_rows.append(row)
    if not inbound_rows:
        return []
    thread = anchor.get("thread_id")
    if isinstance(thread, str) and thread.strip():
        tid = thread.strip()
        same_thread = []
        for row in inbound_rows:
            ctx = _row_ctx(row)
            row_tid = ctx.get("thread_id")
            if isinstance(row_tid, str) and row_tid.strip() == tid:
                same_thread.append(row)
        if same_thread:
            inbound_rows = same_thread
    best = max(inbound_rows, key=lambda r: int(_row_value(r, "id") or 0))
    eid = _row_value(best, "id")
    return [int(eid)] if eid is not None else []


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
    if not pending:
        entry["role"] = "trigger"
    else:
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
