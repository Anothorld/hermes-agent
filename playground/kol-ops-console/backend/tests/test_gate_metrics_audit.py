"""Tests for gate-metrics audit aggregation."""

from __future__ import annotations

import datetime as _dt

from app.gate_metrics_audit import compute_gate_audit_metrics


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


def _insert(conn, *, action: str, target: str, ts: _dt.datetime, payload: dict) -> None:
    import json

    conn.execute(
        "INSERT INTO audit_log (action, target, payload_json, ts) VALUES (?, ?, ?, ?)",
        (action, target, json.dumps(payload), ts.isoformat(timespec="seconds")),
    )
    conn.commit()


def test_first_pass_excludes_refined_reply_draft():
    conn = _seed_conn()
    now = _dt.datetime.now(_dt.timezone.utc)
    base = {"env": "TEST", "identity_id": 1, "campaign_id": "c1"}
    _insert(
        conn,
        action="approval.refine",
        target="approval.reply_draft",
        ts=now - _dt.timedelta(hours=2),
        payload=base,
    )
    _insert(
        conn,
        action="approval.approve",
        target="approval.reply_draft",
        ts=now - _dt.timedelta(hours=1),
        payload=base,
    )
    _insert(
        conn,
        action="approval.approve",
        target="approval.reply_draft",
        ts=now,
        payload={**base, "identity_id": 2, "campaign_id": "c2"},
    )

    out = compute_gate_audit_metrics(conn, env="TEST", days=7)
    assert out["first_pass_decisions_total"] == 1
    assert out["first_pass_approval_rate"] == 1.0
    assert out["reply_decisions_total"] == 2


def test_manual_touch_includes_escalation_resolve():
    conn = _seed_conn()
    now = _dt.datetime.now(_dt.timezone.utc)
    _insert(
        conn,
        action="approval.approve",
        target="approval.reply_draft",
        ts=now,
        payload={"env": "TEST", "campaign_id": "c1", "identity_id": 1},
    )
    _insert(
        conn,
        action="escalation.resolve",
        target="9",
        ts=now,
        payload={"env": "TEST", "campaign_id": "c1", "decision": "resume"},
    )

    out = compute_gate_audit_metrics(conn, env="TEST", days=7)
    assert out["touched_campaign_count"] == 1
    assert out["manual_touchpoints_per_campaign"] == 2.0
