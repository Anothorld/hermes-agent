"""Session lifecycle tags and internal notes via QuickCEP CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from . import cal
from .pii_sanitize import mask_string

log = logging.getLogger(__name__)

_DEFAULT_SKILL_DIR = Path.home() / ".hermes/profiles/povison-cs/skills/social-media/quickcep"
_TAG_MAP_PATH = Path(__file__).resolve().parent / "config" / "session_tag_map.yaml"

HANDOFF_PHASES = frozenset(
    {
        "processing",
        "draft_ready",
        "awaiting_expert",
        "failed",
        "reviewed",
        "followup_while_busy",
        "operator_sent",
    }
)

PHASE_LABELS: dict[str, str] = {
    "processing": "处理中",
    "draft_ready": "草稿待审",
    "awaiting_expert": "待专家",
    "failed": "处理失败",
    "reviewed": "已结案",
    "followup_while_busy": "客户追加消息",
    "operator_sent": "操作员已发送回复",
}

STATUS_BY_PHASE: dict[str, str] = {
    "processing": "processing",
    "draft_ready": "draft_ready",
    "awaiting_expert": "awaiting_expert",
    "failed": "failed",
    "reviewed": "reviewed",
    "operator_sent": "operator_replied",
}

# Monotonic session progression — prevents stale agent handoffs overwriting operator actions.
_STATUS_ORDER: dict[str, int] = {
    "pending": 0,
    "processing": 10,
    "failed": 15,
    "awaiting_expert": 20,
    "skipped": 25,
    "draft_ready": 30,
    "operator_replied": 40,
    "reviewed": 50,
}

# Agent-driven phases that must not run after operator already sent / reviewed.
_STALE_AGENT_PHASES = frozenset({"processing", "draft_ready", "awaiting_expert", "failed"})


@dataclass
class HandoffPlan:
    phase: str
    tags_add: list[str] = field(default_factory=list)
    tags_remove: list[str] = field(default_factory=list)
    note_body: str = ""
    target_status: Optional[str] = None


def _quickcep_skill_dir() -> Path:
    return Path(os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", str(_DEFAULT_SKILL_DIR)))


def load_tag_map() -> dict[str, Any]:
    if not _TAG_MAP_PATH.exists():
        return {}
    with _TAG_MAP_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _valid_tag(tag_id: Any) -> Optional[str]:
    if tag_id is None:
        return None
    s = str(tag_id).strip()
    return s if s else None


def _ai_lifecycle_ids(tag_map: Mapping[str, Any]) -> list[str]:
    ai = tag_map.get("ai_lifecycle") or {}
    ids: list[str] = []
    for val in ai.values():
        tid = _valid_tag(val)
        if tid:
            ids.append(tid)
    return ids


def _business_id(tag_map: Mapping[str, Any], key: str) -> Optional[str]:
    biz = tag_map.get("business") or {}
    return _valid_tag(biz.get(key))


def _ai_id(tag_map: Mapping[str, Any], key: str) -> Optional[str]:
    ai = tag_map.get("ai_lifecycle") or {}
    return _valid_tag(ai.get(key))


def inquiry_tag_for_category(category: Optional[str]) -> Optional[str]:
    """Resolve QuickCEP Inquiry Nature tag id for a classify category."""
    return _inquiry_tag(load_tag_map(), category)


def _phase_status_rank(phase: str) -> int:
    target = STATUS_BY_PHASE.get(phase)
    if not target:
        return 0
    return _STATUS_ORDER.get(target, 0)


def _handoff_stale_for_session(*, phase: str, session_status: str) -> bool:
    """True when applying this phase would regress lifecycle after operator send/review."""
    if phase not in _STALE_AGENT_PHASES:
        return False
    current_rank = _STATUS_ORDER.get(session_status, 0)
    phase_rank = _phase_status_rank(phase)
    return current_rank >= 40 and phase_rank < current_rank


def _inquiry_tag(tag_map: Mapping[str, Any], category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    inquiry = tag_map.get("inquiry_by_category") or {}
    return _valid_tag(inquiry.get(category))


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _compose_standard_note(
    *,
    phase_label: str,
    customer_need: str = "",
    actions_taken: str = "",
    follow_up: str = "",
    operator_hint: str = "",
    extra_lines: Optional[list[str]] = None,
) -> str:
    lines = [
        f"[AI-CS] {_now_label()} | {phase_label}",
        "",
        "【客户需求】",
        f"- {customer_need or '（见会话邮件）'}",
        "",
        "【已处理】",
        f"- {actions_taken or '—'}",
        "",
        "【后续跟进】",
        f"- {follow_up or '—'}",
        "",
        "【接手提示】",
        f"- {operator_hint or '—'}",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)


def _compose_operator_sent_note(ctx: Mapping[str, Any]) -> str:
    operator_id = ctx.get("operator_id") or ctx.get("owner_id") or "—"
    subject = ctx.get("email_subject") or ctx.get("subject") or "—"
    send_note = ctx.get("send_note") or "操作员在 QuickCEP 发送回复"
    prior_hint = ctx.get("operator_hint") or "已回复客户，等待客户反馈"
    return "\n".join(
        [
            f"[AI-CS] {_now_label()} | 操作员已发送回复",
            "",
            "【本次发送】",
            f"- 操作员：{operator_id}",
            f"- 主题：{subject}",
            f"- 说明：{send_note}",
            "",
            "【当前状态】",
            "- 邮件已发出，等待客户回复",
            "",
            "【后续跟进】",
            "- 若客户再来信：系统将自动重新处理",
            "- 若需主动跟进：操作员自行安排",
            "",
            "【接手提示】",
            f"- {prior_hint}",
        ]
    )


def compose_handoff(phase: str, context: Optional[Mapping[str, Any]] = None) -> HandoffPlan:
    """Build tag add/remove list and note body for a lifecycle phase."""
    if phase not in HANDOFF_PHASES:
        raise ValueError(f"unknown handoff phase: {phase}")

    ctx = dict(context or {})
    tag_map = load_tag_map()
    ai_all = _ai_lifecycle_ids(tag_map)
    tags_add: list[str] = []
    tags_remove: list[str] = list(ai_all)

    classify = ctx.get("classify") or {}
    if isinstance(classify, str):
        try:
            classify = json.loads(classify)
        except json.JSONDecodeError:
            classify = {}
    category = classify.get("category") if isinstance(classify, dict) else None
    route = classify.get("route") if isinstance(classify, dict) else None
    urgency = str(ctx.get("urgency") or classify.get("urgency") or "medium")

    customer_need = str(ctx.get("customer_need") or "")
    actions_taken = str(ctx.get("actions_taken") or "")
    follow_up = str(ctx.get("follow_up") or "")
    operator_hint = str(ctx.get("operator_hint") or "")
    error = str(ctx.get("error") or "")

    note_body = ""
    target_status = STATUS_BY_PHASE.get(phase)

    if phase == "processing":
        tid = _ai_id(tag_map, "processing")
        if tid:
            tags_add.append(tid)
        inq = _inquiry_tag(tag_map, category)
        if inq:
            tags_add.append(inq)
        actions = actions_taken or f"分类：{route}/{category}；开始处理 inbound"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "处理完成后更新 draft 或升级",
            operator_hint=operator_hint,
        )

    elif phase == "draft_ready":
        tid = _ai_id(tag_map, "draft_ready")
        if tid:
            tags_add.append(tid)
        ac = _business_id(tag_map, "awaiting_customer")
        if ac:
            tags_add.append(ac)
        esc = _business_id(tag_map, "escalation")
        if esc:
            tags_remove.append(esc)
        actions = actions_taken or "draft-save 完成，待操作员审阅发送"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "操作员：在 QuickCEP 审阅并发送草稿",
            operator_hint=operator_hint or "草稿已保存，请核对后发送",
        )

    elif phase == "awaiting_expert":
        tid = _ai_id(tag_map, "awaiting_expert")
        if tid:
            tags_add.append(tid)
        esc = _business_id(tag_map, "escalation")
        if esc:
            tags_add.append(esc)
        if urgency == "high":
            urg = _business_id(tag_map, "urgent")
            if urg:
                tags_add.append(urg)
        feishu_thread = ctx.get("feishu_thread_id") or ""
        actions = actions_taken or f"已飞书升级 thread={feishu_thread}"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "等待飞书后援回复",
            operator_hint=operator_hint or "升级已发出，请勿直接回复客户",
        )

    elif phase == "failed":
        tid = _ai_id(tag_map, "failed")
        if tid:
            tags_add.append(tid)
        actions = actions_taken or f"处理失败：{error or '未知错误'}"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "Console relaunch 或人工接管",
            operator_hint=operator_hint or "需人工检查 gateway/bridge 日志",
        )

    elif phase == "reviewed":
        tid = _ai_id(tag_map, "closed")
        if tid:
            tags_add.append(tid)
        ac = _business_id(tag_map, "awaiting_customer")
        if ac:
            tags_remove.append(ac)
        dr = _ai_id(tag_map, "draft_ready")
        if dr:
            tags_remove.append(dr)
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions_taken or "Console 标记已审阅",
            follow_up=follow_up or "无进一步 AI 动作",
            operator_hint=operator_hint or "本周期已结案",
        )

    elif phase == "followup_while_busy":
        tags_add = []
        tags_remove = []
        target_status = None
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need or "客户在本轮处理中追加消息",
            actions_taken=actions_taken or "已记录，未重复 launch",
            follow_up=follow_up or "当前 run 完成后会处理最新上下文",
            operator_hint=operator_hint or "客户追加了消息",
        )

    elif phase == "operator_sent":
        tid = _ai_id(tag_map, "closed")
        if tid:
            tags_add.append(tid)
        dr = _ai_id(tag_map, "draft_ready")
        if dr:
            tags_remove.append(dr)
        ac = _business_id(tag_map, "awaiting_customer")
        if ac:
            tags_add.append(ac)
        note_body = _compose_operator_sent_note(ctx)

    # dedupe while preserving order
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    tags_remove = _dedupe([t for t in tags_remove if t not in tags_add])
    tags_add = _dedupe(tags_add)

    return HandoffPlan(
        phase=phase,
        tags_add=tags_add,
        tags_remove=tags_remove,
        note_body=mask_string(note_body),
        target_status=target_status,
    )


def _run_quickcep_cli(args: list[str]) -> tuple[int, str, str]:
    cli = _quickcep_skill_dir() / "scripts" / "quickcep_cli.py"
    if not cli.exists():
        return 1, "", f"quickcep_cli not found: {cli}"
    proc = subprocess.run(
        [sys.executable, str(cli), *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_quickcep_skill_dir()),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _resolve_chat_session_id(
    *,
    quickcep_session_id: str,
    chat_session_id: Optional[str],
) -> Optional[str]:
    if chat_session_id:
        return chat_session_id
    code, out, _ = _run_quickcep_cli(
        ["messages", quickcep_session_id, "--page", "0", "--page-size", "1", "--compact"]
    )
    if code != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    messages = data.get("messages") or data.get("data") or []
    if isinstance(messages, list) and messages:
        row = messages[0]
        if isinstance(row, dict):
            return str(row.get("chatSessionId") or "") or None
    return None


def apply_quickcep_tags(
    *,
    quickcep_session_id: str,
    tags_add: list[str],
    tags_remove: list[str],
) -> list[dict[str, Any]]:
    """Apply tag changes; skips empty IDs; returns per-tag results."""
    results: list[dict[str, Any]] = []
    for tag_id in tags_remove:
        code, out, err = _run_quickcep_cli(["tags-remove", quickcep_session_id, tag_id])
        results.append(
            {"action": "remove", "tag_id": tag_id, "ok": code == 0, "stdout": out, "stderr": err}
        )
        if code != 0:
            log.warning("tags-remove failed session=%s tag=%s: %s", quickcep_session_id, tag_id, err)
    for tag_id in tags_add:
        code, out, err = _run_quickcep_cli(["tags-add", quickcep_session_id, tag_id])
        results.append(
            {"action": "add", "tag_id": tag_id, "ok": code == 0, "stdout": out, "stderr": err}
        )
        if code != 0:
            log.warning("tags-add failed session=%s tag=%s: %s", quickcep_session_id, tag_id, err)
    return results


def apply_quickcep_note(
    *,
    quickcep_session_id: str,
    chat_session_id: str,
    note_body: str,
) -> dict[str, Any]:
    if not note_body.strip():
        return {"ok": True, "skipped": True}
    code, out, err = _run_quickcep_cli(
        [
            "add-note",
            quickcep_session_id,
            "--chat-session-id",
            chat_session_id,
            "--text",
            note_body,
        ]
    )
    ok = code == 0
    if not ok:
        log.warning("add-note failed session=%s: %s", quickcep_session_id, err)
    return {"ok": ok, "stdout": out, "stderr": err}


def apply_handoff(
    *,
    quickcep_session_id: str,
    phase: str,
    env: str = "LIVE",
    context: Optional[Mapping[str, Any]] = None,
    chat_session_id: Optional[str] = None,
    skip_quickcep: bool = False,
) -> dict[str, Any]:
    """Compose and apply lifecycle handoff (tags + note + CAL events)."""
    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return {"ok": False, "error": "session not found"}

    if _handoff_stale_for_session(phase=phase, session_status=str(sess["status"])):
        log.info(
            "skip stale handoff phase=%s session=%s status=%s",
            phase,
            quickcep_session_id,
            sess["status"],
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": f"session already {sess['status']}, skip phase {phase}",
        }

    plan = compose_handoff(phase, context)

    chat_id = chat_session_id or sess.get("chat_session_id")
    chat_id = _resolve_chat_session_id(
        quickcep_session_id=quickcep_session_id,
        chat_session_id=str(chat_id) if chat_id else None,
    )

    tag_results: list[dict[str, Any]] = []
    note_result: dict[str, Any] = {"ok": True, "skipped": True}

    if not skip_quickcep:
        if plan.tags_add or plan.tags_remove:
            tag_results = apply_quickcep_tags(
                quickcep_session_id=quickcep_session_id,
                tags_add=plan.tags_add,
                tags_remove=plan.tags_remove,
            )
        if plan.note_body and chat_id:
            note_result = apply_quickcep_note(
                quickcep_session_id=quickcep_session_id,
                chat_session_id=chat_id,
                note_body=plan.note_body,
            )
        elif plan.note_body and not chat_id:
            note_result = {"ok": False, "error": "chat_session_id missing for add-note"}
            log.warning("handoff note skipped: no chat_session_id for %s", quickcep_session_id)

    if plan.target_status:
        current_rank = _STATUS_ORDER.get(str(sess["status"]), 0)
        target_rank = _STATUS_ORDER.get(plan.target_status, 0)
        if target_rank >= current_rank:
            cal.update_session_status(session_row_id=sess["id"], status=plan.target_status)
        else:
            log.info(
                "skip status regression %s -> %s session=%s phase=%s",
                sess["status"],
                plan.target_status,
                quickcep_session_id,
                phase,
            )

    handoff_payload = {
        "phase": phase,
        "note_text": plan.note_body,
        "tags_add": plan.tags_add,
        "tags_remove": plan.tags_remove,
        "target_status": plan.target_status,
        "tag_results": tag_results,
        "note_result": note_result,
    }
    cal.write_event(
        quickcep_session_id=quickcep_session_id,
        event_type="session_handoff",
        payload=handoff_payload,
        env=env,
    )
    if phase == "operator_sent":
        cal.write_event(
            quickcep_session_id=quickcep_session_id,
            event_type="operator_sent",
            payload={
                "message_id": (context or {}).get("message_id"),
                "operator_id": (context or {}).get("operator_id"),
            },
            env=env,
        )
        msg_id = (context or {}).get("message_id")
        if msg_id:
            cal.write_facts(
                quickcep_session_id=quickcep_session_id,
                namespaces={"handoff": {"last_operator_outbound_id": str(msg_id)}},
                env=env,
            )

    if context and context.get("operator_hint"):
        cal.write_facts(
            quickcep_session_id=quickcep_session_id,
            namespaces={"handoff": {"last_operator_hint": str(context["operator_hint"])}},
            env=env,
        )

    ok = note_result.get("ok", True) and all(r.get("ok", True) for r in tag_results)
    return {
        "ok": ok,
        "plan": {
            "phase": plan.phase,
            "tags_add": plan.tags_add,
            "tags_remove": plan.tags_remove,
            "note_body": plan.note_body,
            "target_status": plan.target_status,
        },
        "tag_results": tag_results,
        "note_result": note_result,
    }


def handle_operator_send(
    info: Mapping[str, Any],
    *,
    env: str | None = None,
) -> dict[str, Any]:
    """Process QuickCEP operatorSendMsg for tracked sessions."""
    env = env or os.environ.get("CS_OPS_ENV", "LIVE")
    session_id = str(info.get("chatSubSessionId") or "")
    message_id = str(info.get("id") or "")
    if not session_id:
        return {"ok": False, "skipped": True, "reason": "no session id"}

    untracked_enabled = os.environ.get("CS_OPS_HANDOFF_UNTRACKED_SENDS", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    sess = cal.get_session(quickcep_session_id=session_id, env=env)
    if not sess:
        if not untracked_enabled:
            return {"ok": False, "skipped": True, "reason": "session not in CAL"}
        return {
            "ok": False,
            "skipped": True,
            "reason": "untracked send: no CAL row (create session via inbound enqueue first)",
        }

    allowed_statuses = {"draft_ready", "awaiting_expert", "processing", "operator_replied"}
    if sess["status"] not in allowed_statuses:
        return {"ok": False, "skipped": True, "reason": f"status={sess['status']}"}

    ctx_data = cal.get_dispatch_context(quickcep_session_id=session_id, env=env) or {}
    facts = ctx_data.get("facts") or {}
    last_out = (facts.get("handoff") or {}).get("last_operator_outbound_id")
    if message_id and last_out and str(last_out) == message_id:
        return {"ok": True, "skipped": True, "reason": "deduped message id"}

    prior_hint = (facts.get("handoff") or {}).get("last_operator_hint") or "已按草稿回复客户"
    context: dict[str, Any] = {
        "message_id": message_id,
        "operator_id": info.get("ownerId") or "",
        "email_subject": info.get("email_subject") or "",
        "operator_hint": prior_hint,
        "send_note": "操作员在 QuickCEP 发送回复",
    }
    if sess["status"] == "draft_ready":
        context["send_note"] = "基于 AI 草稿审阅后发送"
    elif sess["status"] == "processing":
        context["send_note"] = "处理过程中操作员直接发送"

    if info.get("chatSessionId") and not sess.get("chat_session_id"):
        cal.update_session_chat_id(
            session_row_id=sess["id"],
            chat_session_id=str(info["chatSessionId"]),
        )

    return apply_handoff(
        quickcep_session_id=session_id,
        phase="operator_sent",
        env=env,
        context=context,
        chat_session_id=str(info.get("chatSessionId") or sess.get("chat_session_id") or "") or None,
    )
