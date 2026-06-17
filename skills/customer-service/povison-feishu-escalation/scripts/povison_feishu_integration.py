#!/usr/bin/env python3
"""Message formatters for Povison Feishu escalation requests."""

from __future__ import annotations

from typing import Any


def determine_urgency(category: str, contact_count: int = 1) -> str:
    high = {"legal_threat", "social_threat", "vip_discount", "high_value_refund", "safety"}
    medium = {"refund_request", "executive_demand", "b2b_inquiry"}
    if category in high or contact_count >= 3:
        return "high"
    if category in medium:
        return "medium"
    return "low"


def create_escalation_request(
    *,
    escalation_id: str,
    customer_name: str,
    customer_email: str,
    order_number: str,
    problem_description: str,
    attempted_solutions: list[str],
    customer_expectation: str,
    suggested_action: str,
    needed_support: str,
    urgency: str = "medium",
    contact_count: int = 1,
) -> dict[str, Any]:
    icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(urgency, "🟠")
    solutions = "\n".join(f"  • {s}" for s in attempted_solutions) or "  • (none yet)"
    message = f"""[ESC:{escalation_id}] {icon} 升级请求 · {urgency.upper()}

👤 客户: {customer_name} ({customer_email})
📦 订单: {order_number}

❓ 问题:
{problem_description}

🔧 已尝试:
{solutions}

🎯 客户期望:
{customer_expectation}

💡 AI建议:
{suggested_action}

🙏 需要后援组:
{needed_support}

🤖 由AI客服代理自动提交
请 @AI客服 或直接回复本主题"""
    return {"message": message, "urgency": urgency, "escalation_id": escalation_id}


def create_inquiry(
    *,
    escalation_id: str,
    topic: str,
    need_info: str,
    context: str,
    what_i_know: str,
    what_i_dont_know: str,
    urgency: str = "low",
) -> dict[str, Any]:
    icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(urgency, "🟢")
    message = f"""[ESC:{escalation_id}] {icon} 信息咨询 · {urgency.upper()}

📋 主题: {topic}

❓ 需要确认:
{need_info}

📎 背景:
{context}

✅ 已知:
{what_i_know}

❔ 未知:
{what_i_dont_know}

🤖 由AI客服代理自动提交
请 @AI客服 或直接回复本主题"""
    return {"message": message, "urgency": urgency, "escalation_id": escalation_id}
