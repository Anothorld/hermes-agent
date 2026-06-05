"""Backfill and dedup for draft_edit_learning capture."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def _seed_sent_approved_draft(cal_mod, *, identity_id: int, campaign_id: str, env: str) -> None:
    cal_mod.write_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Agent draft paragraph for learning.",
                    "thread_id": "thread-backfill-1",
                },
                "primary_goal": "outreach",
                "primary_lane": "commerce",
                "child_skill": "kol-reply-synthesizer",
                "gmail_draft": {
                    "thread_id": "thread-backfill-1",
                    "message_id": "msg-draft-1",
                    "draft_id": "d1",
                },
            },
        },
        source="test",
        env=env,
    )
    cal_mod.write_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        namespace="offer",
        facts={
            "offer.outreach_sent": True,
            "offer.gmail_sent_thread_id": "thread-backfill-1",
        },
        source="test",
        env=env,
    )


def test_backfill_writes_draft_edit_learning(cal_db, bridge_pkg, monkeypatch):
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="backfill@test", env=env)
    _seed_sent_approved_draft(cal_mod, identity_id=iid, campaign_id="C-BF1", env=env)

    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.resolve_sent_body.return_value = (
        "Operator edited final paragraph for learning.",
        "msg-sent-1",
    )
    monkeypatch.setattr(
        bridge_pkg.gmail_console,
        "list_operator_gmail_clients",
        lambda: [],
    )

    out = gr.backfill_edit_learning(
        env=env,
        client=mock_client,
        dry_run=False,
        limit=50,
    )
    assert out["edit_learning_count"] == 1
    assert out["edited_was_edited_count"] == 1

    with cal_mod._connect() as conn:
        row = conn.execute(
            """SELECT payload_json FROM kol_conversation_events
                WHERE event_type='draft_edit_learning' AND identity_id=? AND env=?""",
            (iid, env),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload.get("was_edited") is True


def test_backfill_skips_when_edit_learning_exists(cal_db, bridge_pkg, monkeypatch):
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="backfill2@test", env=env)
    _seed_sent_approved_draft(cal_mod, identity_id=iid, campaign_id="C-BF2", env=env)
    cal_mod.write_event(
        identity_id=iid,
        campaign_id="C-BF2",
        event_type="draft_edit_learning",
        goal="outreach",
        lane="commerce",
        actor="test",
        payload={"was_edited": True, "edit_distance": 0.2},
        env=env,
    )

    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    monkeypatch.setattr(
        bridge_pkg.gmail_console,
        "list_operator_gmail_clients",
        lambda: [],
    )

    out = gr.backfill_edit_learning(env=env, client=mock_client, dry_run=False, limit=50)
    assert out["edit_learning_count"] == 0
    assert out["skipped_count"] >= 1


def test_capture_suite_includes_backfill_job(bridge_pkg):
    names = bridge_pkg.learning_jobs.resolve_job_names(suite="capture")
    assert "reconcile_sent" in names
    assert "backfill_edit_learning" in names
