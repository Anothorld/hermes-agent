"""Deterministic operator-hub helpers for escalation detail (topic split + steps)."""

from __future__ import annotations

import re
from typing import Any, Mapping


_WORKFLOW_STEP_DEFS: tuple[dict[str, str], ...] = (
    {"n": "1", "title": "阅读来信", "hint": "看清 KOL / 经纪人说了什么"},
    {"n": "2", "title": "填写答复", "hint": "用自然语言写下你的决定"},
    {"n": "3", "title": "查看预览稿", "hint": "核对系统合成的回信（只读）"},
    {"n": "4", "title": "提交并恢复", "hint": "把决定交给 AI 继续推进"},
    {"n": "5", "title": "批准回信", "hint": "创建 Gmail 草稿并发送"},
)


def _topic_label(segment: str) -> str:
    if ":" in segment:
        return segment.split(":", 1)[0].strip() or "话题"
    return "话题"


def _topic_summary(segment: str) -> str:
    if ":" in segment:
        return segment.split(":", 1)[1].strip()
    return segment.strip()


def _topic_needs_decision(segment: str, escalation_id: int) -> bool:
    lower = segment.lower()
    if f"escalation {escalation_id}" in lower:
        return True
    if "operator decision needed" in lower:
        return True
    if "需操作员" in segment or "操作员决定" in segment:
        return True
    if "operator decision" in lower and "needed" in lower:
        return True
    return False


def parse_pending_topic_segments(text: str | None) -> list[str]:
    """Split ``approval.pending_topics`` into display segments."""
    if not isinstance(text, str) or not text.strip():
        return []
    parts: list[str] = []
    for chunk in text.split(";"):
        cleaned = chunk.strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def build_topic_cards(
    escalation: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build operator-facing topic cards for multi-topic inbound escalations."""
    escalation_id = escalation.get("id")
    if not isinstance(escalation_id, int):
        escalation_id = 0
    fact_map = facts if isinstance(facts, dict) else {}
    pending_raw = fact_map.get("approval.pending_topics")
    segments = parse_pending_topic_segments(
        pending_raw if isinstance(pending_raw, str) else None,
    )
    cards: list[dict[str, Any]] = []
    for idx, segment in enumerate(segments):
        needs = _topic_needs_decision(segment, escalation_id)
        cards.append({
            "id": slug_topic_id(_topic_label(segment)) + f"-{idx}",
            "label": _topic_label(segment),
            "summary": _topic_summary(segment),
            "status": "needs_decision" if needs else "auto_reply",
            "status_label": "需你决定" if needs else "可自动回复",
        })
    if cards:
        return cards
    ctx = escalation.get("resume_context") or {}
    kol_quote = ctx.get("kol_quote") if isinstance(ctx, dict) else None
    question = (
        escalation.get("question_to_operator")
        or escalation.get("suggested_question")
        or escalation.get("reason")
        or ""
    )
    summary = str(kol_quote or question).strip()
    if not summary:
        return []
    return [{
        "id": "escalation_trigger",
        "label": "升级触发话题",
        "summary": summary[:800],
        "status": "needs_decision",
        "status_label": "需你决定",
    }]


def _step_status(step_n: int, active_step: int) -> str:
    if step_n < active_step:
        return "done"
    if step_n == active_step:
        return "active"
    return "pending"


def compute_workflow_step(
    *,
    escalation_state: str | None,
    has_draft: bool,
    draft_phase: str | None,
    can_approve: bool,
) -> int:
    """Return 1–5 for the escalation operator hub stepper."""
    state = (escalation_state or "").strip()
    if state == "awaiting_answer":
        if has_draft and draft_phase == "pre_answer":
            return 3
        return 2
    if state in {"answered", "resuming"}:
        return 4
    if state == "resolved":
        if can_approve and has_draft:
            return 5
        return 5
    if state == "aborted":
        return 5
    return 1


def build_workflow_steps(
    *,
    escalation_state: str | None,
    has_draft: bool,
    draft_phase: str | None,
    can_approve: bool,
) -> dict[str, Any]:
    active = compute_workflow_step(
        escalation_state=escalation_state,
        has_draft=has_draft,
        draft_phase=draft_phase,
        can_approve=can_approve,
    )
    steps = []
    for spec in _WORKFLOW_STEP_DEFS:
        n = int(spec["n"])
        steps.append({
            **spec,
            "status": _step_status(n, active),
        })
    return {"active_step": active, "steps": steps}


def build_completion_summary(
    escalation: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
    *,
    has_pending_draft: bool,
) -> dict[str, Any] | None:
    """Surface post-approve / resolved state on the escalation hub page."""
    if has_pending_draft:
        return None
    fact_map = facts if isinstance(facts, dict) else {}
    reply_draft = fact_map.get("approval.reply_draft")
    if isinstance(reply_draft, dict) and reply_draft.get("decision") == "approved":
        gmail_draft = reply_draft.get("gmail_draft")
        draft_id = None
        thread_id = None
        if isinstance(gmail_draft, dict):
            raw_id = gmail_draft.get("draft_id")
            if isinstance(raw_id, str) and raw_id.strip():
                draft_id = raw_id.strip()
            raw_thread = gmail_draft.get("thread_id")
            if isinstance(raw_thread, str) and raw_thread.strip():
                thread_id = raw_thread.strip()
        if not draft_id:
            raw_offer = fact_map.get("offer.gmail_draft_id")
            if isinstance(raw_offer, str) and raw_offer.strip():
                draft_id = raw_offer.strip()
        return {
            "status": "draft_approved",
            "message": "回信已批准。请到 Gmail 草稿箱核对后发送。",
            "gmail_draft_id": draft_id,
            "gmail_thread_id": thread_id,
            "linked_escalation_id": reply_draft.get("linked_escalation_id"),
        }
    state = str(escalation.get("state") or "")
    if state in {"resolved", "aborted"}:
        label = "升级已处理完毕" if state == "resolved" else "升级已终止"
        return {
            "status": "escalation_closed",
            "message": label,
            "gmail_draft_id": None,
            "gmail_thread_id": None,
            "linked_escalation_id": escalation.get("id"),
        }
    return None


def workflow_toast_hint(active_step: int) -> str | None:
    """Short next-action hint after operator actions."""
    hints = {
        2: "下一步：填写「操作员答复」后提交并恢复。",
        3: "下一步：确认预览稿后，点「提交并恢复」。",
        4: "下一步：等待 AI 处理；约 30–60 秒后可批准回信。",
        5: "下一步：在下方「⑤ 批准回信」创建 Gmail 草稿。",
    }
    return hints.get(active_step)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug_topic_id(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:48] or "topic"


__all__ = [
    "build_completion_summary",
    "build_topic_cards",
    "build_workflow_steps",
    "compute_workflow_step",
    "parse_pending_topic_segments",
    "workflow_toast_hint",
]
