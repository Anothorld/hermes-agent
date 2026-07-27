"""Session lifecycle tags and internal notes via QuickCEP CLI."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from . import cal
from .pii_sanitize import mask_string
from .profile_refs import quickcep_skill_dir

log = logging.getLogger(__name__)

_DEFAULT_SKILL_DIR = quickcep_skill_dir()
_TAG_MAP_PATH = Path(__file__).resolve().parent / "config" / "session_tag_map.yaml"

HANDOFF_PHASES = frozenset(
    {
        "processing",
        "draft_ready",
        "awaiting_expert",
        "failed",
        "skipped",
        "reviewed",
        "followup_while_busy",
        "operator_sent",
    }
)

# Agent-invented aliases observed in production logs → canonical phase.
PHASE_ALIASES: dict[str, str] = {
    "completed": "reviewed",
    "processed_by_human": "reviewed",
    "human_processed": "reviewed",
    "done": "reviewed",
    "intentionally_skipped": "skipped",
    "ignore": "skipped",
    "no_action": "skipped",
}


def normalize_handoff_phase(phase: str) -> str:
    """Return canonical handoff phase, mapping known aliases."""
    raw = (phase or "").strip()
    return PHASE_ALIASES.get(raw.lower(), raw)

PHASE_LABELS: dict[str, str] = {
    "processing": "处理中",
    "draft_ready": "草稿待审",
    "awaiting_expert": "待专家",
    "failed": "处理失败",
    "skipped": "无需处理",
    "reviewed": "已结案",
    "followup_while_busy": "客户追加消息",
    "operator_sent": "操作员已发送回复",
}

STATUS_BY_PHASE: dict[str, str] = {
    "processing": "processing",
    "draft_ready": "draft_ready",
    "awaiting_expert": "awaiting_expert",
    "failed": "failed",
    "skipped": "skipped",
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
_STALE_AGENT_PHASES = frozenset({"processing", "draft_ready", "awaiting_expert", "failed", "skipped"})

# Escalation may follow a saved draft in the same agent run — still sync QuickCEP tags.
_ESCALATION_OVERRIDE_STATUSES = frozenset({"draft_ready", "processing", "pending"})

_ROUTE_ZH: dict[str, str] = {
    "auto_handle": "自动处理",
    "escalate": "升级专家",
    "review": "待复核",
}

_CATEGORY_ZH: dict[str, str] = {
    "logistics": "物流咨询",
    "product": "产品咨询",
    "issue_standard": "标准售后问题",
    "vip_discount": "VIP 折扣诉求",
    "high_value_refund": "高额退款",
    "refund_request": "退款申请",
    "legal_threat": "法律威胁",
    "social_threat": "社交媒体曝光威胁",
    "executive_demand": "要求管理层介入",
    "b2b_inquiry": "企业/批发咨询",
    "forced": "强制升级",
    "unclear": "意图不明",
}

# Replace common English tokens agents paste into handoff fields (longest match first).
_NOTE_TERM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bescalation resume\s*\+\s*draft-save\b", re.I), "专家回复已合并并生成草稿"),
    (re.compile(r"\bMerged Feishu expert answer;\s*draft-save\b", re.I), "已合并飞书专家答复并生成草稿"),
    (re.compile(r"\bdraft-save\b", re.I), "草稿已生成"),
    (re.compile(r"\bdraft save\b", re.I), "草稿已生成"),
    (re.compile(r"\binbound\b", re.I), "客户来信"),
    (re.compile(r"\bConsole\b"), "工单列表"),
    (re.compile(r"\bgateway\b", re.I), "自动处理"),
    (re.compile(r"\bbridge\b", re.I), ""),
    (re.compile(r"\bthread\s*=", re.I), "线索编号："),
    (re.compile(r"\bQuickCEP\b", re.I), "会话后台"),
    (re.compile(r"\bAI\b"), "智能客服"),
    (re.compile(r"\brelaunch\b", re.I), "重新处理"),
    (re.compile(r"\blookup\b", re.I), "查询"),
    (re.compile(r"\bescalate\b", re.I), "升级专家"),
    (re.compile(r"\bauto_handle\b", re.I), "自动处理"),
    (re.compile(r"\breview\b", re.I), "待复核"),
)


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


def _legacy_draft_mode() -> bool:
    """M3 transition mode: drafts written to QuickCEP (not CAL).

    When true, ``draft_ready`` without a CAL draft is legitimate (the draft lives
    in QuickCEP). Gated by the same env that switches ``draft-save`` to the legacy
    QuickCEP path (§4.13 "仍需坚守: 过渡期 M3 模式").
    """
    return os.environ.get("CS_OPS_DRAFT_SAVE_LEGACY_QUICKCEP", "").strip().lower() in ("1", "true", "yes")


def _quickcep_handoff_side_effect_skip_reason(*, phase: str, session_status: str) -> Optional[str]:
    """When set, ``apply_handoff`` must not post QuickCEP tags or internal notes."""
    if phase == "followup_while_busy":
        return "followup_while_busy is CAL-only (no QuickCEP note)"
    if phase == "awaiting_expert" and session_status in _ESCALATION_OVERRIDE_STATUSES:
        return None
    target = STATUS_BY_PHASE.get(phase)
    if target and session_status == target:
        return f"session already {session_status}"
    if phase in _STALE_AGENT_PHASES:
        current_rank = _STATUS_ORDER.get(session_status, 0)
        phase_rank = _phase_status_rank(phase)
        if phase_rank < current_rank:
            return f"phase {phase} would regress from {session_status}"
    return None


def _status_update_allowed(*, current_status: str, target_status: Optional[str], phase: str) -> bool:
    if not target_status:
        return False
    current_rank = _STATUS_ORDER.get(current_status, 0)
    target_rank = _STATUS_ORDER.get(target_status, 0)
    if target_rank >= current_rank:
        return True
    return phase == "awaiting_expert" and current_status in _ESCALATION_OVERRIDE_STATUSES


def _agent_debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        import time as _time

        with open("/Users/arnold/agent_prj/.cursor/debug-400546.log", "a", encoding="utf-8") as _f:
            _f.write(
                json.dumps(
                    {
                        "sessionId": "400546",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion


def _inquiry_tag(tag_map: Mapping[str, Any], category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    inquiry = tag_map.get("inquiry_by_category") or {}
    return _valid_tag(inquiry.get(category))


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M 协调世界时")


def _zh_route(route: Optional[str]) -> str:
    if not route:
        return "未分类"
    return _ROUTE_ZH.get(str(route), str(route))


def _zh_category(category: Optional[str]) -> str:
    if not category:
        return "未分类"
    return _CATEGORY_ZH.get(str(category), str(category))


def _localize_note_fragment(text: str) -> str:
    """Normalize handoff field text to Chinese-friendly wording."""
    if not text:
        return text
    out = text
    for pattern, replacement in _NOTE_TERM_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    for key, label in sorted(_ROUTE_ZH.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"\b{re.escape(key)}\b", label, out, flags=re.I)
    for key, label in sorted(_CATEGORY_ZH.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"\b{re.escape(key)}\b", label, out, flags=re.I)
    return out.strip()


# Strip engineering/CLI details — internal notes are CS-operator facing only.
_OPERATOR_NOTE_SANITIZE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"--[\w-]+(?:=\S*)?"), ""),
    (re.compile(r"\bmessage_id\s*[:=]\s*\S+", re.I), ""),
    (re.compile(r"\brun_id\s*[:=]\s*\S+", re.I), ""),
    (re.compile(r"\bthread_id\s*[:=]\s*\S+", re.I), ""),
    (re.compile(r"(?:需人工)?检查(?:网关|桥接服务|系统)(?:与(?:网关|桥接服务|系统))?(?:日志)?"), "请人工处理"),
    (re.compile(r"网关(?:与|和)?桥接服务(?:日志)?"), ""),
    (re.compile(r"桥接服务"), ""),
    (re.compile(r"网关"), ""),
    (re.compile(r"launch returned no run_id", re.I), "未能自动处理"),
    (re.compile(r"gateway launch failed", re.I), "未能自动处理"),
    (re.compile(r"watcher\s+", re.I), ""),
    (re.compile(r"[\w/\\]+\.(?:py|html|sh|json)\b"), ""),
    (re.compile(r"\s{2,}"), " "),
    (re.compile(r"[；;]\s*[；;]+"), "；"),
)


def _sanitize_operator_note(text: str) -> str:
    """Keep only CS-relevant business wording in QuickCEP internal notes."""
    if not text:
        return text
    out = _localize_note_fragment(text)
    for pattern, replacement in _OPERATOR_NOTE_SANITIZE:
        out = pattern.sub(replacement, out)
    out = re.sub(r"\s+", " ", out).strip(" ；;，,")
    return out.strip()


# Agent sometimes uses --phase failed for intentional out-of-scope skips (B2B spam, carrier
# COI misroute, SEO pitches). Remap to skipped so QuickCEP gets AI-已结案, not AI-处理失败.
_INTENTIONAL_SKIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"b2b", re.I),
    re.compile(r"垃圾|spam|seo|推广|推销|guest\s*post", re.I),
    re.compile(r"承运商|carrier|coi\b|gomwd", re.I),
    re.compile(r"误入|无关|不在处理范围|无需处理|不处理|不生成.*草稿", re.I),
    re.compile(r"拉黑名单|拉黑域名|关闭工单", re.I),
    re.compile(r"销售邮件|pitch|vendor\s*outreach", re.I),
)


def _intentional_skip_text(context: Optional[Mapping[str, Any]]) -> str:
    ctx = context or {}
    parts = [
        str(ctx.get("actions_taken") or ""),
        str(ctx.get("error") or ""),
        str(ctx.get("customer_need") or ""),
        str(ctx.get("follow_up") or ""),
        str(ctx.get("operator_hint") or ""),
    ]
    classify = ctx.get("classify") or {}
    if isinstance(classify, dict):
        parts.append(str(classify.get("category") or ""))
        parts.append(str(classify.get("route") or ""))
    return " ".join(parts)


def is_intentional_skip_context(context: Optional[Mapping[str, Any]]) -> bool:
    """True when handoff text indicates a deliberate no-reply skip (not a real failure)."""
    blob = _intentional_skip_text(context)
    if not blob.strip():
        return False
    return any(p.search(blob) for p in _INTENTIONAL_SKIP_PATTERNS)


def _strip_failure_prefix(text: str) -> str:
    return re.sub(r"^处理失败[：:]\s*", "", (text or "").strip())


def _skipped_actions_summary(context: Mapping[str, Any]) -> str:
    actions = _strip_failure_prefix(str(context.get("actions_taken") or ""))
    if actions:
        return _sanitize_operator_note(actions)
    err = _strip_failure_prefix(str(context.get("error") or ""))
    if err:
        return _sanitize_operator_note(err)
    return "已识别为无需 AI 处理的来信（B2B/垃圾/承运商误入等）"


def maybe_remap_failed_to_skipped(
    phase: str,
    context: Optional[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Remap agent ``failed`` handoffs that describe intentional skips."""
    if phase != "failed" or not is_intentional_skip_context(context):
        return phase, dict(context or {})
    ctx = dict(context or {})
    log.info(
        "remap failed handoff -> skipped (intentional skip) actions=%s",
        (ctx.get("actions_taken") or "")[:80],
    )
    return "skipped", ctx


def _business_failure_summary(*, error: str = "", actions_taken: str = "") -> str:
    """Map technical failures to operator-facing business summaries."""
    sanitized_actions = _sanitize_operator_note(actions_taken)
    if sanitized_actions and not re.search(
        r"(?:--|run_id|message_id|launch|gateway|watcher|\.py\b|被解析为命令)",
        sanitized_actions,
        re.I,
    ):
        if sanitized_actions.startswith("处理失败"):
            return sanitized_actions
        return f"处理失败：{sanitized_actions}"

    err = (error or "").lower()
    if "draft" in err or "草稿" in err or "content-file" in err:
        return "处理失败：未能自动生成回复草稿，需人工撰写并发送"
    if any(tok in err for tok in ("launch", "gateway", "run_id", "watcher")):
        return "处理失败：未能自动处理该会话，需人工查看客户来信"
    if "classify" in err or "intent" in err:
        return "处理失败：未能识别客户诉求，需人工判断并回复"
    return "处理失败：未能自动完成客户诉求处理，需人工接管"


def _note_header(phase_label: str) -> str:
    return f"[智能客服] {_now_label()} | {phase_label}"


def _compose_standard_note(
    *,
    phase_label: str,
    customer_need: str = "",
    actions_taken: str = "",
    follow_up: str = "",
    operator_hint: str = "",
    extra_lines: Optional[list[str]] = None,
) -> str:
    customer_need = _sanitize_operator_note(customer_need)
    actions_taken = _sanitize_operator_note(actions_taken)
    follow_up = _sanitize_operator_note(follow_up)
    operator_hint = _sanitize_operator_note(operator_hint)
    lines = [
        _note_header(phase_label),
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
    send_note = _sanitize_operator_note(
        str(ctx.get("send_note") or "操作员已发送回复邮件")
    )
    prior_hint = _sanitize_operator_note(
        str(ctx.get("operator_hint") or "已回复客户，等待客户反馈")
    )
    return "\n".join(
        [
            _note_header("操作员已发送回复"),
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
    phase = normalize_handoff_phase(phase)
    if phase not in HANDOFF_PHASES:
        allowed = ", ".join(sorted(HANDOFF_PHASES))
        raise ValueError(f"unknown handoff phase: {phase!r}; allowed: {allowed}")

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
        route_zh = _zh_route(str(route) if route else None)
        category_zh = _zh_category(str(category) if category else None)
        actions = actions_taken or f"已识别为{category_zh}，{route_zh}；正在处理客户来信"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "处理完成后会生成回复草稿或升级给内部专家",
            operator_hint=operator_hint,
        )

    elif phase == "draft_ready":
        tid = _ai_id(tag_map, "draft_ready")
        if tid:
            tags_add.append(tid)
        # Do NOT add "awaiting_customer" here — the draft has not been sent yet.
        # The operator_sent phase adds it after the reply is actually sent.
        esc = _business_id(tag_map, "escalation")
        if esc:
            tags_remove.append(esc)
        actions = actions_taken or "已查询相关信息并生成回复草稿"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "草稿已在工单台生成，请审阅后发送",
            operator_hint=operator_hint or "草稿已在工单台生成，请审阅后发送",
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
        if actions_taken:
            actions = actions_taken
        elif feishu_thread:
            actions = f"已升级至内部专家处理（升级编号：{feishu_thread}）"
        else:
            actions = "已升级至内部专家处理"
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "等待内部专家给出处理意见",
            operator_hint=operator_hint or "已升级给专家，请勿直接回复客户",
        )

    elif phase == "failed":
        tid = _ai_id(tag_map, "failed")
        if tid:
            tags_add.append(tid)
        actions = _business_failure_summary(error=error, actions_taken=actions_taken)
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need,
            actions_taken=actions,
            follow_up=follow_up or "请人工查看客户来信并回复；如需重试可在工单列表重新处理",
            operator_hint=operator_hint or "自动处理未完成，请根据客户诉求人工跟进",
        )

    elif phase == "skipped":
        tid = _ai_id(tag_map, "closed")
        if tid:
            tags_add.append(tid)
        inv = _business_id(tag_map, "invalid_ticket")
        if inv:
            tags_add.append(inv)
        actions = _skipped_actions_summary(ctx)
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need or "无关或误入邮件，不在 AI 处理范围",
            actions_taken=actions,
            follow_up=follow_up or "无需回复客户；可在 QuickCEP 关闭工单或拉黑发件域名",
            operator_hint=operator_hint or "本单 AI 已跳过，无需接手",
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
            actions_taken=actions_taken or "本单已标记为处理完毕",
            follow_up=follow_up or "暂无后续自动动作",
            operator_hint=operator_hint or "本周期已结案",
        )

    elif phase == "followup_while_busy":
        tags_add = []
        tags_remove = []
        target_status = None
        note_body = _compose_standard_note(
            phase_label=PHASE_LABELS[phase],
            customer_need=customer_need or "客户在本轮处理中追加消息",
            actions_taken=actions_taken or "已记录客户新消息，待当前处理完成后一并纳入",
            follow_up=follow_up or "当前处理完成后会自动纳入最新来信",
            operator_hint=operator_hint or "客户追加了消息，请关注是否需调整回复",
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
    force_quickcep_tags: bool = False,
) -> dict[str, Any]:
    """Compose and apply lifecycle handoff (tags + note + CAL events).

    ``force_quickcep_tags`` bypasses the "session already at target status"
    QuickCEP-tag skip. Use it when the caller advanced the CAL status to the
    target value directly (via ``cal.update_session_status``) *before* invoking
    the handoff — in that case no prior handoff applied the QuickCEP tags, so
    the skip guard would otherwise drop them silently (e.g. the watcher's
    launch-failed path sets ``status=failed`` then calls ``apply_handoff``).
    """
    canonical = normalize_handoff_phase(phase)
    if canonical != phase:
        log.info(
            "handoff phase alias %r -> %r session=%s",
            phase,
            canonical,
            quickcep_session_id,
        )
        phase = canonical

    ctx = dict(context or {})
    phase, ctx = maybe_remap_failed_to_skipped(phase, ctx)

    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return {"ok": False, "error": "session not found"}

    if _handoff_stale_for_session(phase=phase, session_status=str(sess["status"])):
        completion_result = None
        if phase in ("draft_ready", "failed", "skipped"):
            try:
                from .escalation_completion import complete_resuming_escalation_after_handoff

                completion_result = complete_resuming_escalation_after_handoff(
                    quickcep_session_id=quickcep_session_id,
                    phase=phase,
                    env=env,
                    operator_hint=str(ctx.get("operator_hint") or ""),
                )
            except Exception as exc:
                log.warning(
                    "escalation completion on stale handoff failed session=%s: %s",
                    quickcep_session_id,
                    exc,
                )
        log.info(
            "skip stale handoff phase=%s session=%s status=%s",
            phase,
            quickcep_session_id,
            sess["status"],
        )
        out = {
            "ok": True,
            "skipped": True,
            "reason": f"session already {sess['status']}, skip phase {phase}",
        }
        if completion_result:
            out["escalation_completion"] = completion_result
        return out

    # Bridge guard (§4.13 B): draft_ready requires a CAL draft — the agent must
    # call draft-save (contract step 5) before apply-handoff draft_ready (step 6).
    # Refuse when cs_session.draft_html is empty so Console always has a draft to
    # show. Skipped in M3 legacy mode (drafts written to QuickCEP, not CAL).
    if phase == "draft_ready" and not _legacy_draft_mode():
        if not (sess.get("draft_html") or "").strip():
            log.warning(
                "block draft_ready handoff: no CAL draft session=%s (agent skipped draft-save)",
                quickcep_session_id,
            )
            return {
                "ok": False,
                "error": "draft_ready_requires_cal_draft",
                "error_detail": (
                    "apply-handoff --phase draft_ready 前必须先 cs_bridge_tool draft-save 把草稿保存到 CAL；"
                    "当前 cs_session.draft_html 为空，Console 无草稿可展示。请先执行 draft-save 再重试 draft_ready。"
                ),
                "session_id": quickcep_session_id,
            }

    plan = compose_handoff(phase, ctx)

    # PR1.2: persist the classify dict {category, route, confidence, urgency} to
    # cs_facts so the workbench L1 aggregate can surface it without a QuickCEP call.
    try:
        classify = ctx.get("classify")
        if isinstance(classify, str):
            classify = json.loads(classify)
        if isinstance(classify, dict) and classify:
            cal.write_facts(
                quickcep_session_id=quickcep_session_id,
                namespaces={"classify": {
                    "category": classify.get("category"),
                    "route": classify.get("route"),
                    "confidence": classify.get("confidence"),
                    "urgency": classify.get("urgency"),
                }},
                env=env,
            )
    except Exception as exc:
        log.debug("classify fact write failed session=%s: %s", quickcep_session_id, exc)

    # PR2: schedule an Autopilot send job on draft_ready (no-op when disabled).
    if phase == "draft_ready":
        try:
            from .autopilot import on_draft_ready

            on_draft_ready(quickcep_session_id=quickcep_session_id, env=env)
        except Exception as exc:
            log.debug("autopilot on_draft_ready failed session=%s: %s", quickcep_session_id, exc)

    chat_id = chat_session_id or sess.get("chat_session_id")
    chat_id = _resolve_chat_session_id(
        quickcep_session_id=quickcep_session_id,
        chat_session_id=str(chat_id) if chat_id else None,
    )

    qc_skip_reason = _quickcep_handoff_side_effect_skip_reason(
        phase=phase,
        session_status=str(sess["status"]),
    )
    # When the caller pre-advanced the CAL status to the target value directly
    # (not via a prior apply_handoff), the "session already at target" skip
    # would wrongly drop tags that were never actually written. Override it.
    if force_quickcep_tags and qc_skip_reason and qc_skip_reason.startswith("session already"):
        log.info(
            "force quickcep tags session=%s phase=%s (override skip: %s)",
            quickcep_session_id,
            phase,
            qc_skip_reason,
        )
        qc_skip_reason = None
    _agent_debug_log(
        hypothesis_id="B",
        location="session_handoff.py:apply_handoff",
        message="handoff skip evaluation",
        data={
            "quickcep_session_id": quickcep_session_id,
            "phase": phase,
            "session_status": str(sess["status"]),
            "qc_skip_reason": qc_skip_reason,
        },
    )
    if qc_skip_reason:
        log.info(
            "skip quickcep tags/note session=%s phase=%s: %s",
            quickcep_session_id,
            phase,
            qc_skip_reason,
        )
        _agent_debug_log(
            hypothesis_id="D",
            location="session_handoff.py:apply_handoff",
            message="quickcep side effects skipped",
            data={
                "quickcep_session_id": quickcep_session_id,
                "phase": phase,
                "session_status": str(sess["status"]),
                "reason": qc_skip_reason,
            },
        )
        skip_quickcep = True

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
            _agent_debug_log(
                hypothesis_id="E",
                location="session_handoff.py:apply_handoff",
                message="quickcep note posted",
                data={
                    "quickcep_session_id": quickcep_session_id,
                    "phase": phase,
                    "session_status": str(sess["status"]),
                },
            )
        elif plan.note_body and not chat_id:
            note_result = {"ok": False, "error": "chat_session_id missing for add-note"}
            log.warning("handoff note skipped: no chat_session_id for %s", quickcep_session_id)

    if plan.target_status:
        if _status_update_allowed(
            current_status=str(sess["status"]),
            target_status=plan.target_status,
            phase=phase,
        ):
            cal.update_session_status(session_row_id=sess["id"], status=plan.target_status)
            if phase == "processing":
                try:
                    cal.stamp_agent_processing_at(session_row_id=sess["id"])
                except Exception as exc:
                    log.debug("stamp_agent_processing_at failed session=%s: %s", quickcep_session_id, exc)
        else:
            log.info(
                "skip status regression %s -> %s session=%s phase=%s",
                sess["status"],
                plan.target_status,
                quickcep_session_id,
                phase,
            )

    via_resume = False
    escalation_id = None
    if phase in ("draft_ready", "failed", "skipped"):
        try:
            esc = cal.get_resuming_escalation_for_session(
                quickcep_session_id=quickcep_session_id, env=env,
            )
            if esc:
                via_resume = True
                escalation_id = esc.get("id")
        except Exception as exc:
            log.debug("via_resume lookup failed session=%s: %s", quickcep_session_id, exc)

    handoff_payload = {
        "phase": phase,
        "note_text": plan.note_body,
        "tags_add": plan.tags_add,
        "tags_remove": plan.tags_remove,
        "target_status": plan.target_status,
        "tag_results": tag_results,
        "note_result": note_result,
        "via_resume": via_resume,
        "escalation_id": escalation_id,
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

    completion_result = None
    try:
        from .escalation_completion import complete_resuming_escalation_after_handoff

        completion_result = complete_resuming_escalation_after_handoff(
            quickcep_session_id=quickcep_session_id,
            phase=phase,
            env=env,
            operator_hint=str((context or {}).get("operator_hint") or ""),
        )
    except Exception as exc:
        log.warning("escalation completion hook failed session=%s: %s", quickcep_session_id, exc)

    ok = note_result.get("ok", True) and all(r.get("ok", True) for r in tag_results)
    result = {
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
    if completion_result:
        result["escalation_completion"] = completion_result
    return result


def _maybe_close_escalations_after_operator_send(
    *,
    session_id: str,
    env: str,
    operator_hint: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort ESC close when operator send is deduped or after successful handoff."""
    if not cal.session_has_open_escalation(quickcep_session_id=session_id, env=env):
        return result
    try:
        from .operator_escalation_close import close_escalations_on_operator_manual_reply

        esc_close = close_escalations_on_operator_manual_reply(
            quickcep_session_id=session_id,
            env=env,
            operator_hint=operator_hint,
        )
        if esc_close.get("closed"):
            result["escalation_close"] = esc_close
    except Exception as exc:
        log.warning(
            "escalation close on operator send failed session=%s: %s",
            session_id,
            exc,
        )
    return result


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
    prior_hint = (facts.get("handoff") or {}).get("last_operator_hint") or "已按草稿回复客户"
    last_out = (facts.get("handoff") or {}).get("last_operator_outbound_id")
    if message_id and last_out and str(last_out) == message_id:
        return _maybe_close_escalations_after_operator_send(
            session_id=session_id,
            env=env,
            operator_hint=prior_hint,
            result={"ok": True, "skipped": True, "reason": "deduped message id"},
        )
    context: dict[str, Any] = {
        "message_id": message_id,
        "operator_id": info.get("ownerId") or "",
        "email_subject": info.get("email_subject") or "",
        "operator_hint": prior_hint,
        "send_note": "操作员在会话后台发送回复",
    }
    if sess["status"] == "draft_ready":
        context["send_note"] = "审阅智能客服草稿后发送"
    elif sess["status"] == "awaiting_expert":
        context["send_note"] = "升级待专家期间人工直接回复客户"
    elif sess["status"] == "processing":
        context["send_note"] = "处理过程中人工直接回复客户"

    if info.get("chatSessionId") and not sess.get("chat_session_id"):
        cal.update_session_chat_id(
            session_row_id=sess["id"],
            chat_session_id=str(info["chatSessionId"]),
        )

    result = apply_handoff(
        quickcep_session_id=session_id,
        phase="operator_sent",
        env=env,
        context=context,
        chat_session_id=str(info.get("chatSessionId") or sess.get("chat_session_id") or "") or None,
    )
    if result.get("ok") and not result.get("skipped"):
        return _maybe_close_escalations_after_operator_send(
            session_id=session_id,
            env=env,
            operator_hint=str(context.get("send_note") or ""),
            result=result,
        )
    return result
