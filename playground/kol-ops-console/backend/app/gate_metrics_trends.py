"""Time-bucketed gate-metrics trends from Console audit_log."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from collections import defaultdict
from typing import Any, Final

from .gate_metrics_audit import (
    REPLY_DRAFT_TARGET,
    _TOUCH_ACTIONS,
    _approval_pair_key,
    _had_prior_refine,
)

VALID_BUCKETS: Final[frozenset[str]] = frozenset({"day", "week", "month", "year"})

DEFAULT_PERIODS: dict[str, int] = {
    "day": 30,
    "week": 12,
    "month": 12,
    "year": 5,
}

MAX_PERIODS: dict[str, int] = {
    "day": 90,
    "week": 52,
    "month": 36,
    "year": 10,
}


def _parse_ts(ts: str | None) -> _dt.datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bucket_key(dt: _dt.datetime, bucket: str) -> str:
    if bucket == "day":
        return dt.strftime("%Y-%m-%d")
    if bucket == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if bucket == "month":
        return dt.strftime("%Y-%m")
    if bucket == "year":
        return dt.strftime("%Y")
    return dt.strftime("%Y-%m-%d")


def _shift_anchor(bucket: str, anchor: _dt.datetime, steps_back: int) -> _dt.datetime:
    if bucket == "day":
        return anchor - _dt.timedelta(days=steps_back)
    if bucket == "week":
        return anchor - _dt.timedelta(weeks=steps_back)
    if bucket == "month":
        month = anchor.month - steps_back
        year = anchor.year
        while month <= 0:
            month += 12
            year -= 1
        day = min(anchor.day, 28)
        return anchor.replace(year=year, month=month, day=day)
    if bucket == "year":
        return anchor.replace(year=anchor.year - steps_back)
    return anchor


def ordered_bucket_labels(*, bucket: str, periods: int) -> list[str]:
    """Return oldest→newest bucket labels for the last ``periods`` units."""
    anchor = _dt.datetime.now(_dt.timezone.utc)
    return [
        _bucket_key(_shift_anchor(bucket, anchor, steps_back), bucket)
        for steps_back in range(periods - 1, -1, -1)
    ]


def _lookback_days(bucket: str, periods: int) -> int:
    if bucket == "day":
        return periods + 2
    if bucket == "week":
        return periods * 7 + 7
    if bucket == "month":
        return periods * 31 + 7
    return periods * 366 + 7


def _empty_slot() -> dict[str, Any]:
    return {
        "first_pass_approved": 0,
        "first_pass_total": 0,
        "live_decisions": 0,
        "live_rejected": 0,
        "handle_seconds": 0.0,
        "handle_samples": 0,
        "resolved_count": 0,
        "terminated_count": 0,
        "touches_by_campaign": defaultdict(int),
    }


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return float(numerator) / float(denominator)


def compute_audit_metric_trends(
    conn: sqlite3.Connection,
    *,
    env: str,
    bucket: str = "week",
    periods: int | None = None,
) -> dict[str, Any]:
    """Aggregate audit_log gate metrics into time buckets."""
    bucket_norm = bucket if bucket in VALID_BUCKETS else "week"
    period_count = periods if periods is not None else DEFAULT_PERIODS[bucket_norm]
    period_count = max(1, min(int(period_count), MAX_PERIODS[bucket_norm]))
    labels = ordered_bucket_labels(bucket=bucket_norm, periods=period_count)
    label_set = set(labels)
    slots: dict[str, dict[str, Any]] = {label: _empty_slot() for label in labels}

    lookback = _lookback_days(bucket_norm, period_count)
    rows = conn.execute(
        "SELECT action, target, payload_json, ts FROM audit_log "
        "WHERE ts >= datetime('now', ?) ORDER BY id ASC",
        (f"-{lookback} day",),
    ).fetchall()
    env_norm = env.upper()

    refine_by_pair: dict[tuple[int | None, str | None], list[_dt.datetime]] = defaultdict(list)
    for row in rows:
        action = str(row["action"] or "")
        target = str(row["target"] or "")
        payload = json.loads(row["payload_json"] or "{}")
        if not isinstance(payload, dict):
            continue
        payload_env = str(payload.get("env") or env_norm).upper()
        if payload_env != env_norm:
            continue
        if action != "approval.refine" or target != REPLY_DRAFT_TARGET:
            continue
        decided_dt = _parse_ts(row["ts"])
        if decided_dt is None:
            continue
        refine_by_pair[_approval_pair_key(payload)].append(decided_dt)

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
        key = _bucket_key(decided_dt, bucket_norm)
        if key not in label_set:
            continue
        slot = slots[key]

        if action in {"approval.approve", "approval.reject"} and target == REPLY_DRAFT_TARGET:
            pair = _approval_pair_key(payload)
            if not _had_prior_refine(refine_by_pair.get(pair, []), decided_at=decided_dt):
                slot["first_pass_total"] += 1
                if action == "approval.approve":
                    slot["first_pass_approved"] += 1
            if payload_env == "LIVE":
                slot["live_decisions"] += 1
                if action == "approval.reject":
                    slot["live_rejected"] += 1

        if action in _TOUCH_ACTIONS:
            cid = payload.get("campaign_id")
            if isinstance(cid, str) and cid:
                slot["touches_by_campaign"][cid] += 1

        if action == "escalation.resolve":
            slot["resolved_count"] += 1
            if payload.get("decision") == "terminate":
                slot["terminated_count"] += 1

        if action in {"approval.approve", "approval.reject", "escalation.resolve"}:
            opened_raw = payload.get("opened_at") or payload.get("created_at")
            opened_dt = _parse_ts(opened_raw) if isinstance(opened_raw, str) else None
            if opened_dt is not None:
                delta = (decided_dt - opened_dt).total_seconds()
                if delta >= 0:
                    slot["handle_seconds"] += delta
                    slot["handle_samples"] += 1

    def _series(label: str, value: float | None) -> dict[str, Any]:
        return {"bucket": label, "value": value}

    first_pass = [
        _series(
            label,
            _rate(slots[label]["first_pass_approved"], slots[label]["first_pass_total"]),
        )
        for label in labels
    ]
    avg_handle = [
        _series(
            label,
            (
                slots[label]["handle_seconds"] / 60.0 / slots[label]["handle_samples"]
                if slots[label]["handle_samples"]
                else None
            ),
        )
        for label in labels
    ]
    live_reject = [
        _series(
            label,
            _rate(slots[label]["live_rejected"], slots[label]["live_decisions"]),
        )
        for label in labels
    ]
    termination = [
        _series(
            label,
            _rate(slots[label]["terminated_count"], slots[label]["resolved_count"]),
        )
        for label in labels
    ]
    manual_touch = []
    for label in labels:
        touches = slots[label]["touches_by_campaign"]
        if touches:
            avg = sum(touches.values()) / len(touches)
            manual_touch.append(_series(label, avg))
        else:
            manual_touch.append(_series(label, None))

    return {
        "env": env_norm,
        "bucket": bucket_norm,
        "periods": period_count,
        "series": {
            "first_pass_approval_rate": first_pass,
            "avg_handle_minutes": avg_handle,
            "live_incident_rate": live_reject,
            "termination_rate": termination,
            "manual_touchpoints_per_campaign": manual_touch,
        },
    }
