"""Tests for gate-metrics audit trend bucketing."""

from __future__ import annotations

import datetime as _dt

from app.gate_metrics_trends import compute_audit_metric_trends, ordered_bucket_labels


def _seed_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            payload_json TEXT,
            ts TEXT NOT NULL
        )"""
    )
    return conn


def _insert(
    conn,
    *,
    action: str,
    target: str,
    ts: _dt.datetime,
    payload: dict,
) -> None:
    import json

    conn.execute(
        "INSERT INTO audit_log (action, target, payload_json, ts) VALUES (?, ?, ?, ?)",
        (action, target, json.dumps(payload), ts.isoformat(timespec="seconds")),
    )
    conn.commit()


def test_ordered_bucket_labels_week():
    labels = ordered_bucket_labels(bucket="week", periods=3)
    assert len(labels) == 3
    assert all("-W" in label for label in labels)


def test_audit_trend_first_pass_rate_by_day():
    conn = _seed_conn()
    now = _dt.datetime.now(_dt.timezone.utc)
    day_a = (now - _dt.timedelta(days=2)).replace(hour=12, minute=0, second=0)
    day_b = (now - _dt.timedelta(days=1)).replace(hour=12, minute=0, second=0)
    base = {"env": "TEST", "campaign_id": "c1", "identity_id": 1, "opened_at": day_a.isoformat()}

    _insert(
        conn,
        action="approval.approve",
        target="approval.reply_draft",
        ts=day_a,
        payload=base,
    )
    _insert(
        conn,
        action="approval.reject",
        target="approval.reply_draft",
        ts=day_b,
        payload={**base, "identity_id": 2, "campaign_id": "c2"},
    )
    _insert(
        conn,
        action="approval.approve",
        target="approval.reply_draft",
        ts=day_b,
        payload={**base, "identity_id": 3, "campaign_id": "c3"},
    )

    out = compute_audit_metric_trends(conn, env="TEST", bucket="day", periods=7)
    series = out["series"]["first_pass_approval_rate"]
    by_bucket = {row["bucket"]: row["value"] for row in series}

    key_a = day_a.strftime("%Y-%m-%d")
    key_b = day_b.strftime("%Y-%m-%d")
    assert by_bucket[key_a] == 1.0
    assert by_bucket[key_b] == 0.5
