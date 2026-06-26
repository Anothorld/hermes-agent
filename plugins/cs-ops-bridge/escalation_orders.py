"""Deterministic order context for Feishu escalation messages."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_WEC_ORDER_RE = re.compile(r"WEC[A-Z0-9]{5,}", re.IGNORECASE)
_NUM_TOKEN_RE = re.compile(r"\d{15,20}")


def is_probable_quickcep_session_id(digits: str) -> bool:
    """QuickCEP chatSubSessionId values are typically 19-digit ids starting with 254."""
    return len(digits) == 19 and digits.isdigit() and digits.startswith("254")


def is_probable_numeric_order_id(digits: str) -> bool:
    """Accept storefront order ids; reject QuickCEP session-shaped numbers."""
    if not digits.isdigit() or not (15 <= len(digits) <= 20):
        return False
    if is_probable_quickcep_session_id(digits):
        return False
    return True


def extract_order_ids_from_text(
    *parts: str | None,
    exclude_ids: Iterable[str] | None = None,
) -> list[str]:
    """Pull Povison-style order ids mentioned in escalation text."""
    excluded = {str(value).strip() for value in (exclude_ids or ()) if str(value).strip()}
    found: list[str] = []
    for part in parts:
        text = str(part or "")
        for match in _WEC_ORDER_RE.finditer(text):
            oid = match.group(0).strip()
            if oid and oid not in excluded and oid not in found:
                found.append(oid)
        for match in _NUM_TOKEN_RE.finditer(text):
            oid = match.group(0).strip()
            if not is_probable_numeric_order_id(oid):
                continue
            if oid in excluded or oid in found:
                continue
            found.append(oid)
    return found[:5]


def fetch_escalation_order_context(
    *,
    quickcep_session_id: str,
    text_hints: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Best-effort QuickCEP orders + Povison tracking for escalation notify."""
    from .cal import _fetch_visitor_orders
    from .order_tracking import fetch_tracking_prefill

    order_ctx = _fetch_visitor_orders(quickcep_session_id)
    orders = order_ctx.get("orders") if isinstance(order_ctx.get("orders"), list) else []
    order_ids = [
        str(item.get("orderId") or "").strip()
        for item in orders
        if isinstance(item, dict) and str(item.get("orderId") or "").strip()
    ]
    hint_ids = extract_order_ids_from_text(
        *(text_hints or ()),
        exclude_ids=[quickcep_session_id],
    )
    for oid in hint_ids:
        if oid not in order_ids:
            order_ids.append(oid)

    tracking: dict[str, Any] = {}
    if order_ids:
        tracking = fetch_tracking_prefill(order_ids)

    return {
        "orders": orders,
        "tracking": tracking,
        "hint_order_ids": hint_ids,
        "source": order_ctx.get("source"),
        "error": order_ctx.get("error"),
        "intention_tags": order_ctx.get("intention_tags") or [],
    }


def format_order_section(ctx: Mapping[str, Any] | None) -> str:
    """Operator-facing Chinese order block for Feishu escalation posts."""
    if not ctx:
        return "（QuickCEP 未查到关联订单）"

    orders = ctx.get("orders") if isinstance(ctx.get("orders"), list) else []
    tracking = ctx.get("tracking") if isinstance(ctx.get("tracking"), dict) else {}
    summaries = tracking.get("summaries") if isinstance(tracking.get("summaries"), list) else []
    if not orders and not summaries:
        err = str(ctx.get("error") or "").strip()
        if err:
            return f"（QuickCEP 未查到关联订单 — {err}）"
        return "（QuickCEP 未查到关联订单；请结合下方摘要中的订单号）"

    by_id = {
        str(item.get("orderId") or "").strip(): item
        for item in summaries
        if isinstance(item, dict) and str(item.get("orderId") or "").strip()
    }

    lines: list[str] = []
    rendered_ids: set[str] = set()
    for order in orders[:3]:
        if not isinstance(order, dict):
            continue
        oid = str(order.get("orderId") or "").strip() or "（未知订单号）"
        rendered_ids.add(oid)
        parts = [f"· {oid}"]
        fin = str(order.get("financialStatus") or "").strip()
        ful = str(order.get("fulfillmentStatus") or "").strip()
        if fin or ful:
            parts.append(f"支付:{fin or '?'} / 履约:{ful or '?'}")
        total = str(order.get("totalPrice") or "").strip()
        if total:
            currency = str(order.get("currency") or "USD").strip()
            parts.append(f"金额:{total} {currency}")
        items = order.get("lineItems") if isinstance(order.get("lineItems"), list) else []
        titles = [
            str(item.get("title") or "").strip()[:48]
            for item in items[:2]
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ]
        if titles:
            parts.append("商品: " + "；".join(titles))

        track = by_id.get(oid) or {}
        if track.get("found"):
            if track.get("trackingNumber"):
                parts.append(f"运单:{track['trackingNumber']}")
            if track.get("status"):
                parts.append(f"物流:{track['status']}")
            earliest = str(track.get("earliestEdd") or "").strip()
            latest = str(track.get("latestEdd") or "").strip()
            if earliest or latest:
                parts.append(f"预计送达:{earliest or '?'} ~ {latest or '?'}")
            latest_event = track.get("latestEvent") if isinstance(track.get("latestEvent"), dict) else {}
            desc = str(latest_event.get("description") or "").strip()
            if desc:
                parts.append(f"最新节点:{desc[:80]}")

        lines.append(" ".join(parts))

    for track in summaries[:5]:
        if not isinstance(track, dict):
            continue
        oid = str(track.get("orderId") or "").strip()
        if not oid or oid in rendered_ids:
            continue
        rendered_ids.add(oid)
        parts = [f"· {oid}"]
        if track.get("found"):
            if track.get("trackingNumber"):
                parts.append(f"运单:{track['trackingNumber']}")
            if track.get("status"):
                parts.append(f"物流:{track['status']}")
            earliest = str(track.get("earliestEdd") or "").strip()
            latest = str(track.get("latestEdd") or "").strip()
            if earliest or latest:
                parts.append(f"预计送达:{earliest or '?'} ~ {latest or '?'}")
        else:
            parts.append("（物流系统未命中，可能为内部单号）")
        lines.append(" ".join(parts))

    return "\n".join(lines) if lines else "（QuickCEP 未查到关联订单）"
