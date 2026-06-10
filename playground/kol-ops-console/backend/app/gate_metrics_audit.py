"""Gate-metrics aggregation from Console ``audit_log``."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Final

REPLY_DRAFT_TARGET: Final[str] = "approval.reply_draft"

_TOUCH_ACTIONS: Final[frozenset[str]] = frozenset({
    "approval.approve",
    "approval.reject",
    "approval.refine",
    "escalation.resolve",
    "escalation.preview_draft",
})


def _parse_ts(ts: str | None) -> _dt.datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _approval_pair_key(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    identity_id = payload.get("identity_id")
    campaign_id = payload.get("campaign_id")
    iid = int(identity_id) if isinstance(identity_id, int) else None
    cid = str(campaign_id) if isinstance(campaign_id, str) and campaign_id else None
    return iid, cid


def _had_prior_refine(
    refine_events: list[_dt.datetime],
    *,
    decided_at: _dt.datetime,
) -> bool:
    return any(ts < decided_at for ts in refine_events)


def compute_gate_audit_metrics(
    conn: sqlite3.Connection,
    *,
    env: str,
    days: int,
) -> dict[str, Any]:
    """Aggregate approval/escalation audit metrics for the gate dashboard."""
    rows = conn.execute(
        "SELECT action, target, payload_json, ts FROM audit_log "
        "WHERE ts >= datetime('now', ?) ORDER BY id ASC",
        (f"-{int(days)} day",),
    ).fetchall()
    env_norm = env.upper()

    refine_by_pair: dict[tuple[int | None, str | None], list[_dt.datetime]] = defaultdict(list)
    tag_counter: Counter[str] = Counter()
    touches_by_campaign: dict[str, int] = defaultdict(int)

    first_pass_approved = 0
    first_pass_total = 0
    reply_approved = 0
    reply_total = 0
    live_rejected = 0
    live_decisions = 0
    total_handle_seconds = 0.0
    total_handle_samples = 0
    terminated_count = 0
    resolved_count = 0

    for row in rows:
        action = str(row["action"] or "")
        target = str(row["target"] or "")
        payload = json.loads(row["payload_json"] or "{}")
        if not isinstance(payload, dict):
            payload = {}
        payload_env = str(payload.get("env") or env_norm).upper()
        if payload_env != env_norm:
            continue

        decided_dt = _parse_ts(row["ts"])
        if decided_dt is None:
            continue

        if action == "approval.refine" and target == REPLY_DRAFT_TARGET:
            refine_by_pair[_approval_pair_key(payload)].append(decided_dt)

        if action in {"approval.approve", "approval.reject"}:
            if target == REPLY_DRAFT_TARGET:
                reply_total += 1
                if action == "approval.approve":
                    reply_approved += 1
                pair = _approval_pair_key(payload)
                if not _had_prior_refine(refine_by_pair.get(pair, []), decided_at=decided_dt):
                    first_pass_total += 1
                    if action == "approval.approve":
                        first_pass_approved += 1
            if payload_env == "LIVE":
                live_decisions += 1
                if action == "approval.reject":
                    live_rejected += 1

        if action in _TOUCH_ACTIONS:
            cid = payload.get("campaign_id")
            if isinstance(cid, str) and cid:
                touches_by_campaign[cid] += 1

        if action == "approval.reject":
            for tag in payload.get("reason_tags") or []:
                if isinstance(tag, str) and tag.strip():
                    tag_counter[tag.strip().lower()] += 1

        if action == "escalation.resolve":
            resolved_count += 1
            if payload.get("decision") == "terminate":
                terminated_count += 1
            for tag in payload.get("reason_tags") or []:
                if isinstance(tag, str) and tag.strip():
                    tag_counter[tag.strip().lower()] += 1

        if action in {"approval.approve", "approval.reject", "escalation.resolve"}:
            opened_raw = payload.get("opened_at") or payload.get("created_at")
            opened_dt = _parse_ts(opened_raw) if isinstance(opened_raw, str) else None
            if opened_dt is not None:
                delta = (decided_dt - opened_dt).total_seconds()
                if delta >= 0:
                    total_handle_seconds += delta
                    total_handle_samples += 1

    avg_manual_touches = 0.0
    if touches_by_campaign:
        avg_manual_touches = sum(touches_by_campaign.values()) / len(touches_by_campaign)

    return {
        "first_pass_approval_rate": (
            first_pass_approved / first_pass_total if first_pass_total else 0.0
        ),
        "reply_approval_rate": (
            reply_approved / reply_total if reply_total else 0.0
        ),
        "first_pass_decisions_total": first_pass_total,
        "reply_decisions_total": reply_total,
        "avg_handle_minutes": (
            total_handle_seconds / 60.0 / total_handle_samples
            if total_handle_samples else 0.0
        ),
        "handle_time_samples": total_handle_samples,
        "manual_touchpoints_per_campaign": avg_manual_touches,
        "touched_campaign_count": len(touches_by_campaign),
        "termination_rate": (
            terminated_count / resolved_count if resolved_count else 0.0
        ),
        "live_reject_rate": (
            live_rejected / live_decisions if live_decisions else 0.0
        ),
        "top_rejection_tags": [
            {"tag": tag, "count": count}
            for tag, count in tag_counter.most_common(10)
        ],
    }
