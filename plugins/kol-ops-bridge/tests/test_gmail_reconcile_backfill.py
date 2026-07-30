"""Backfill and dedup for draft_edit_learning capture."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


@contextmanager
def _noop_gmail_lock():
    yield


def _patch_gmail_lock(monkeypatch, bridge_pkg) -> None:
    monkeypatch.setattr(
        bridge_pkg.gmail_reconcile,
        "_gmail_reconcile_lock",
        _noop_gmail_lock,
    )


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


def _mock_reconcile_gmail(*, sent_body: str, sent_message_id: str) -> MagicMock:
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.list_sent_thread_ids.return_value = ["thread-reconcile-dup"]
    mock_client.resolve_sent_body.return_value = (sent_body, sent_message_id)
    mock_client.get_profile_email.return_value = "ops@example.com"
    return mock_client


def test_sent_reconcile_dedupes_edit_learning_by_message_id(
    cal_db, bridge_pkg, monkeypatch,
):
    """Repeated sent-reconcile ticks must not inflate learning backlog."""
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="reconcile-dup@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-RD1",
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Agent draft paragraph for learning.",
                    "thread_id": "thread-reconcile-dup",
                },
                "primary_goal": "outreach",
                "primary_lane": "commerce",
                "child_skill": "kol-reply-synthesizer",
                "gmail_draft": {
                    "thread_id": "thread-reconcile-dup",
                    "message_id": "msg-draft-dup-1",
                    "draft_id": "d1",
                },
            },
        },
        source="test",
        env=env,
    )

    mock_client = _mock_reconcile_gmail(
        sent_body="Operator final body with meaningful edits.",
        sent_message_id="msg-sent-dup-1",
    )
    monkeypatch.setattr(
        bridge_pkg.gmail_console,
        "list_operator_gmail_clients",
        lambda: [],
    )
    _patch_gmail_lock(monkeypatch, bridge_pkg)

    out1 = gr.run_reconcile_sent(env=env, client=mock_client)
    out2 = gr.run_reconcile_sent(env=env, client=mock_client)
    assert out1["edit_learning_count"] == 1
    assert out2["edit_learning_count"] == 0

    with cal_mod._connect() as conn:
        count = conn.execute(
            """SELECT COUNT(*) FROM kol_conversation_events
                WHERE event_type='draft_edit_learning' AND identity_id=? AND env=?""",
            (iid, env),
        ).fetchone()[0]
    assert count == 1


def test_sent_reconcile_skips_bounce_body(cal_db, bridge_pkg, monkeypatch):
    """DSN body must not write edit-learning or last_outbound terms; still mark sent.

    Thread is already in SENT (operator clicked Send). Delivery bounce text must
    not block ``offer.outreach_sent`` or the kanban stays on 「Draft 待发送」.
    """
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="reconcile-bounce@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-BNC1",
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Agent draft paragraph for learning.",
                    "thread_id": "thread-bounce-1",
                },
                "primary_goal": "outreach",
                "primary_lane": "commerce",
                "child_skill": "kol-reply-synthesizer",
                "gmail_draft": {
                    "thread_id": "thread-bounce-1",
                    "message_id": "msg-draft-bnc-1",
                    "draft_id": "d1",
                },
            },
        },
        source="test",
        env=env,
    )

    mock_client = _mock_reconcile_gmail(
        sent_body="** Address not found **\n\nYour message wasn't delivered to kol@x.com",
        sent_message_id="msg-bounce-dsn-1",
    )
    mock_client.list_sent_thread_ids.return_value = ["thread-bounce-1"]
    monkeypatch.setattr(
        bridge_pkg.gmail_console,
        "list_operator_gmail_clients",
        lambda: [],
    )
    _patch_gmail_lock(monkeypatch, bridge_pkg)

    out = gr.run_reconcile_sent(env=env, client=mock_client)
    assert out["edit_learning_count"] == 0
    assert out["reconciled_count"] == 1

    facts = cal_mod.latest_facts_for(identity_id=iid, campaign_id="C-BNC1", env=env)
    assert facts.get("offer.outreach_sent") is True
    assert facts.get("offer.outreach_sent_at")
    assert not facts.get("offer.last_outbound_terms_proposed")


def test_sent_reconcile_skips_learning_when_thread_has_bounce(
    cal_db, bridge_pkg, monkeypatch,
):
    """Delivery failure in thread skips edit-learning but still marks outreach_sent."""
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="reconcile-thread-bnc@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-TBNC1",
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Agent draft paragraph for learning.",
                    "thread_id": "thread-thread-bnc-1",
                },
                "primary_goal": "outreach",
                "primary_lane": "commerce",
                "child_skill": "kol-reply-synthesizer",
                "gmail_draft": {
                    "thread_id": "thread-thread-bnc-1",
                    "message_id": "msg-draft-tbnc-1",
                    "draft_id": "d1",
                },
            },
        },
        source="test",
        env=env,
    )

    mock_client = _mock_reconcile_gmail(
        sent_body="Hi KOL, operator final body after light edits.",
        sent_message_id="msg-real-sent-tbnc-1",
    )
    mock_client.list_sent_thread_ids.return_value = ["thread-thread-bnc-1"]
    mock_client.get_thread = lambda _tid: [
        {
            "id": "msg-real-sent-tbnc-1",
            "from": "ops@brand.com",
            "body": "Hi KOL, operator final body after light edits.",
            "labels": ["SENT"],
        },
        {
            "id": "msg-bounce-tbnc-1",
            "from": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
            "body": "** Address not found **\n\nYour message wasn't delivered to kol@x.com",
            "labels": ["INBOX"],
        },
    ]
    monkeypatch.setattr(
        bridge_pkg.gmail_console,
        "list_operator_gmail_clients",
        lambda: [],
    )
    _patch_gmail_lock(monkeypatch, bridge_pkg)

    out = gr.run_reconcile_sent(env=env, client=mock_client)
    assert out["edit_learning_count"] == 0
    assert out["reconciled_count"] == 1
    facts = cal_mod.latest_facts_for(identity_id=iid, campaign_id="C-TBNC1", env=env)
    assert facts.get("offer.outreach_sent") is True
    assert facts.get("offer.outreach_sent_at")
    assert "operator final body" in str(facts.get("offer.last_outbound_terms_proposed") or "")


def test_sent_reconcile_marks_sent_when_body_unavailable(
    cal_db, bridge_pkg, monkeypatch,
):
    """SENT membership alone must flip outreach_sent even if body extract fails."""
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="reconcile-nobody@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-NB1",
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Agent draft paragraph for learning.",
                    "thread_id": "thread-nobody-1",
                },
                "primary_goal": "outreach",
                "primary_lane": "commerce",
                "child_skill": "kol-reply-synthesizer",
                "gmail_draft": {
                    "thread_id": "thread-nobody-1",
                    "message_id": "msg-draft-nb-1",
                    "draft_id": "d1",
                },
            },
        },
        source="test",
        env=env,
    )

    mock_client = _mock_reconcile_gmail(sent_body="", sent_message_id="")
    mock_client.list_sent_thread_ids.return_value = ["thread-nobody-1"]
    monkeypatch.setattr(
        bridge_pkg.gmail_console,
        "list_operator_gmail_clients",
        lambda: [],
    )
    _patch_gmail_lock(monkeypatch, bridge_pkg)

    out = gr.run_reconcile_sent(env=env, client=mock_client)
    assert out["reconciled_count"] == 1
    assert out["edit_learning_count"] == 0
    assert out.get("skip_reasons", {}).get(
        "learning_skip:no_agent_or_sent_body_or_bounce", 0,
    ) == 1
    facts = cal_mod.latest_facts_for(identity_id=iid, campaign_id="C-NB1", env=env)
    assert facts.get("offer.outreach_sent") is True
    assert facts.get("offer.outreach_sent_at")


def test_sent_reconcile_first_claim_unbound_mailbox(
    cal_db, bridge_pkg, monkeypatch,
):
    """Unbound campaign + Console operator uid>0 may claim on SENT hit."""
    cal_mod = cal_db
    gr = bridge_pkg.gmail_reconcile
    mr = bridge_pkg.mailbox_resolver
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="reconcile-claim@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-CL1",
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Agent draft paragraph for learning.",
                    "thread_id": "thread-claim-1",
                },
                "primary_goal": "outreach",
                "primary_lane": "commerce",
                "child_skill": "kol-reply-synthesizer",
                "gmail_draft": {
                    "thread_id": "thread-claim-1",
                    "message_id": "msg-draft-cl-1",
                    "draft_id": "d1",
                },
            },
        },
        source="test",
        env=env,
    )

    mock_client = _mock_reconcile_gmail(
        sent_body="Operator final body with meaningful edits.",
        sent_message_id="msg-sent-claim-1",
    )
    mock_client.list_sent_thread_ids.return_value = ["thread-claim-1"]
    _patch_gmail_lock(monkeypatch, bridge_pkg)

    out = gr.run_reconcile_sent(
        env=env, client=mock_client, mailbox_user_id=42,
    )
    assert out["reconciled_count"] == 1
    facts = cal_mod.latest_facts_for(identity_id=iid, campaign_id="C-CL1", env=env)
    assert facts.get("offer.outreach_sent") is True
    binding = mr.read_binding(identity_id=iid, campaign_id="C-CL1", env=env)
    assert binding is not None
    assert binding.user_id == 42


def test_outreach_sent_false_blocked_after_confirmed_send(cal_db):
    """Bounce classifier must not reset offer.outreach_sent once sent_at exists."""
    cal_mod = cal_db
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="sent-guard@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-SG1",
        namespace="offer",
        facts={
            "offer.outreach_sent": True,
            "offer.outreach_sent_at": "2026-06-11T03:19:22+00:00",
        },
        source="gmail:sent-reconcile",
        env=env,
    )
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-SG1",
        namespace="offer",
        facts={"offer.outreach_sent": False},
        source="email:19eb2a04aedcd0b1",
        env=env,
    )
    facts = cal_mod.latest_facts_for(identity_id=iid, campaign_id="C-SG1", env=env)
    assert facts.get("offer.outreach_sent") is True
    assert facts.get("offer.outreach_sent_at") == "2026-06-11T03:19:22+00:00"


def test_list_approved_reply_drafts_skips_when_sent_at_set(cal_db):
    """Reconcile queue must not re-enqueue after a confirmed send timestamp."""
    cal_mod = cal_db
    env = "LIVE"
    iid = cal_mod.upsert_identity(primary_handle="sent-at-skip@test", env=env)
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-SAT1",
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "approved",
                "draft": {
                    "subject": "Test",
                    "to": "kol@example.com",
                    "body": "Hi",
                    "thread_id": "t1",
                },
                "gmail_draft": {"thread_id": "t1", "message_id": "m1"},
            },
        },
        source="test",
        env=env,
    )
    cal_mod.write_facts(
        identity_id=iid,
        campaign_id="C-SAT1",
        namespace="offer",
        facts={
            "offer.outreach_sent": False,
            "offer.outreach_sent_at": "2026-06-11T03:19:22+00:00",
        },
        source="email:19eb2a04aedcd0b1",
        env=env,
    )
    rows = cal_mod.list_approved_reply_drafts(env=env)
    assert not any(
        r["identity_id"] == iid and r["campaign_id"] == "C-SAT1" for r in rows
    )
