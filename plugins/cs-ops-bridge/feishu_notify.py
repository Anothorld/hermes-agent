"""Deterministic Feishu escalation notifications (bridge-owned, not agent send_message)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .escalation_context import resolve_customer_email
from .escalation_orders import fetch_escalation_order_context, format_order_section
from .feishu_client import FeishuSendResult, escalation_chat_id, send_group_text

log = logging.getLogger(__name__)

DEFAULT_ESCALATION_CHAT = "oc_0cdfe1f385b7e839bc147fd99915fe91"  # AI客服后援 (from gateway logs)

# Bridge-owned Feishu texts — poller must ignore these as operator input.
FEISHU_SYSTEM_MESSAGE_PREFIXES: tuple[str, ...] = (
    "[ESC:",
    "[ESC-DONE:",
    "[ESC-LOCK:",
    "[ESC-FAILED:",
)


def is_system_escalation_message(text: str) -> bool:
    stripped = (text or "").strip()
    return any(stripped.startswith(prefix) for prefix in FEISHU_SYSTEM_MESSAGE_PREFIXES)


@dataclass(frozen=True)
class FeishuEscalationContent:
    customer_email: str
    email_summary: str
    email_quote: str


def validate_feishu_notify_inputs(
    *,
    auto_send_feishu: bool,
    escalation_message: Optional[str],
    customer_email: Optional[str],
    email_summary: Optional[str],
    email_quote: Optional[str],
    quickcep_session_id: str,
    env: str,
) -> FeishuEscalationContent:
    """Ensure agent supplied email + bilingual excerpt when bridge posts to Feishu."""
    if escalation_message or not auto_send_feishu:
        return FeishuEscalationContent(
            customer_email=(customer_email or "").strip(),
            email_summary=(email_summary or "").strip(),
            email_quote=(email_quote or "").strip(),
        )

    # email_quote now carries the customer's full original email text (not a partial excerpt).
    summary = (email_summary or "").strip()
    if not summary:
        raise ValueError(
            "email_summary required: agent must provide a Simplified Chinese summary when escalating"
        )

    quote = (email_quote or "").strip()
    if not quote:
        raise ValueError(
            "email_quote required: agent must provide the customer's full original email text"
        )

    email = (customer_email or "").strip()
    if not email:
        email = resolve_customer_email(quickcep_session_id=quickcep_session_id, env=env)
    if not email:
        raise ValueError(
            "customer_email required: pass --customer-email from get-messages when escalating"
        )
    return FeishuEscalationContent(
        customer_email=email,
        email_summary=summary,
        email_quote=quote,
    )


def build_escalation_text(
    *,
    escalation_id: int,
    customer_email: str,
    reason: str,
    urgency: str = "medium",
    question_to_operator: Optional[str] = None,
    email_summary: Optional[str] = None,
    email_quote: Optional[str] = None,
    extra_message: Optional[str] = None,
    order_section: Optional[str] = None,
) -> str:
    icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(urgency, "🟠")
    email_line = customer_email.strip() if customer_email else "（未知）"
    lines = [
        f"[ESC:{escalation_id}] {icon} CS 升级 · {urgency.upper()}",
        "",
        f"客户邮箱: {email_line}",
        f"原因: {reason.strip()}",
    ]
    order_text = (order_section or "").strip()
    if order_text:
        lines.extend(["", "📦 订单信息:", order_text])
    summary = (email_summary or "").strip()
    quote = (email_quote or "").strip()
    if summary or quote:
        lines.extend(["", "📩 客户来信摘要:"])
        if summary:
            lines.append(summary)
        if quote:
            lines.extend(["", "原始来信：", quote])
    if question_to_operator and question_to_operator.strip():
        lines.extend(["", "❓ 需要后援确认:", question_to_operator.strip()])
    if extra_message and extra_message.strip():
        lines.extend(["", extra_message.strip()])
    from .escalation_attachment_vault import vault_upload_notice_or_fallback

    lines.append(vault_upload_notice_or_fallback(escalation_id=escalation_id))
    lines.extend(["", "🤖 由 cs-ops-bridge 自动提交", "请直接回复本主题（仅采纳首位专家回复）"])
    return "\n".join(lines)


def notify_escalation_opened(
    *,
    escalation_id: int,
    quickcep_session_id: str,
    reason: str,
    urgency: str = "medium",
    question_to_operator: Optional[str] = None,
    customer_email: Optional[str] = None,
    email_summary: Optional[str] = None,
    email_quote: Optional[str] = None,
    escalation_message: Optional[str] = None,
    feishu_chat_id: Optional[str] = None,
    env: str = "LIVE",
    auto_send_feishu: bool = True,
) -> FeishuSendResult:
    """Send escalation card to the configured Feishu group."""
    chat_id = (feishu_chat_id or escalation_chat_id() or DEFAULT_ESCALATION_CHAT).strip()
    if escalation_message:
        # Custom body bypasses bridge template — still append vault upload link + SOP.
        text = escalation_message.strip()
        from .escalation_attachment_vault import vault_upload_notice_or_fallback

        text += vault_upload_notice_or_fallback(escalation_id=escalation_id)
    else:
        content = validate_feishu_notify_inputs(
            auto_send_feishu=auto_send_feishu,
            escalation_message=escalation_message,
            customer_email=customer_email,
            email_summary=email_summary,
            email_quote=email_quote,
            quickcep_session_id=quickcep_session_id,
            env=env,
        )
        order_ctx = fetch_escalation_order_context(
            quickcep_session_id=quickcep_session_id,
            text_hints=[reason, question_to_operator or "", email_summary or "", email_quote or ""],
        )
        text = build_escalation_text(
            escalation_id=escalation_id,
            customer_email=content.customer_email,
            reason=reason,
            urgency=urgency,
            question_to_operator=question_to_operator,
            email_summary=content.email_summary,
            email_quote=content.email_quote,
            order_section=format_order_section(order_ctx),
        )
    result = send_group_text(chat_id=chat_id, text=text)
    if result.ok:
        log.info(
            "feishu escalation sent esc=%s chat=%s msg=%s thread=%s",
            escalation_id,
            chat_id,
            result.message_id,
            result.thread_id,
        )
    else:
        log.error(
            "feishu escalation send failed esc=%s chat=%s err=%s",
            escalation_id,
            chat_id,
            result.error,
        )
    return result


def notify_escalation_locked(
    *,
    escalation_id: int,
    feishu_root_message_id: str,
    token: Optional[str] = None,
) -> FeishuSendResult:
    """Reply on the escalation post: first answer accepted, thread closed for further replies."""
    from .feishu_client import reply_to_message

    text = (
        f"[ESC-LOCK:{escalation_id}] 🔒 已采纳首位专家回复\n\n"
        "本条升级已关闭，不再受理新的回复。AI 正在合并意见并生成 QuickCEP 草稿。"
    )
    return reply_to_message(message_id=feishu_root_message_id, text=text, token=token)


def notify_vault_upload_link(
    *,
    escalation_id: int,
    feishu_root_message_id: str,
    token: Optional[str] = None,
) -> FeishuSendResult:
    """Thread reply with vault upload URL (backfill when opening post omitted the link)."""
    from .escalation_attachment_vault import vault_upload_notice_or_fallback
    from .feishu_client import reply_to_message

    body = vault_upload_notice_or_fallback(escalation_id=escalation_id).strip()
    text = f"[ESC:{escalation_id}] 📎 附件上传链接补发\n\n{body}"
    return reply_to_message(message_id=feishu_root_message_id, text=text, token=token)


def notify_escalation_completed(
    *,
    escalation_id: int,
    quickcep_session_id: str,
    outcome: str,
    operator_hint: str = "",
    feishu_chat_id: Optional[str] = None,
    is_retry: bool = False,
) -> FeishuSendResult:
    """Standalone group notice after resume handoff — not a reply thread; poller ignores [ESC-DONE:]."""
    chat_id = (feishu_chat_id or escalation_chat_id() or DEFAULT_ESCALATION_CHAT).strip()
    if outcome == "failed":
        status_line = "❌ 处理失败，请人工接手 QuickCEP 会话"
    elif outcome == "skipped":
        status_line = "⏭️ 已跳过（无需 AI 处理），升级关闭"
    elif outcome == "operator_manual_reply":
        status_line = "✅ 客服已在 QuickCEP 直接回复客户，升级关闭"
    else:
        status_line = "✅ 已处理完成，QuickCEP 草稿待审"
    retry_tag = "（重试）" if is_retry else ""
    lines = [
        f"[ESC-DONE:{escalation_id}]{retry_tag} {status_line}",
        "",
        f"QuickCEP 会话: {quickcep_session_id}",
    ]
    hint = (operator_hint or "").strip()
    if hint:
        lines.extend(["", f"摘要: {hint}"])
    lines.extend(["", "🤖 系统通知 · 请勿回复本消息"])
    return send_group_text(chat_id=chat_id, text="\n".join(lines))


def notify_escalation_resume_failed(
    *,
    escalation_id: int,
    quickcep_session_id: str,
    feishu_message_id: str = "",
    reason: str = "",
    is_retry: bool = False,
) -> FeishuSendResult:
    """Notify operators that a resume run ended without applying handoff.

    Replies on the original escalation Feishu thread when ``feishu_message_id``
    is available; otherwise the caller should rely solely on the CAL event
    (console-only escalations have no Feishu thread).
    """
    retry_tag = "（重试后）" if is_retry else ""
    text = (
        f"[ESC-FAILED:{escalation_id}]{retry_tag} ⚠️ resume agent 未完成草稿生成"
        f"（模型输出异常），需人工介入。\n\n"
        f"原因: {reason or 'run ended without handoff'}\n"
        f"QuickCEP 会话: {quickcep_session_id}\n\n"
        "可在 Console 点「重新生成」重试。"
    )
    msg_id = (feishu_message_id or "").strip()
    if not msg_id:
        log.warning(
            "notify_escalation_resume_failed: no feishu_message_id esc=%s — "
            "skipping Feishu, relying on CAL event only",
            escalation_id,
        )
        return FeishuSendResult(ok=False, message_id="", error="no_feishu_thread")
    from .feishu_client import reply_to_message

    return reply_to_message(message_id=msg_id, text=text)
