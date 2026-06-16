"""Unit tests for reply_chase policy."""

from __future__ import annotations

import datetime as dt


def test_proceed_normal_without_prior_draft(bridge_pkg):
    rc = bridge_pkg.reply_chase
    out = rc.evaluate_chase(
        reply_draft_fact=None,
        reply_draft_captured_at=None,
        inbound_message_id="MSG2",
        inbound_thread_id="TH1",
    )
    assert out["recommended_action"] == "proceed_normal"
    assert out["prior_pending_draft"] is False


def test_skip_same_source(bridge_pkg):
    rc = bridge_pkg.reply_chase
    fact = {
        "decision": "pending",
        "source_message_id": "MSG1",
        "draft": {"thread_id": "TH1", "subject": "Re:", "body": "x", "to": "a@b.com"},
    }
    out = rc.evaluate_chase(
        reply_draft_fact=fact,
        reply_draft_captured_at="2026-06-02T00:00:00+00:00",
        inbound_message_id="MSG1",
        inbound_thread_id="TH1",
        now=dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc),
    )
    assert out["recommended_action"] == "skip_same_source"


def test_regenerate_on_follow_up_same_thread(bridge_pkg):
    rc = bridge_pkg.reply_chase
    fact = {
        "decision": "pending",
        "source_message_id": "MSG1",
        "draft": {"thread_id": "TH1", "subject": "Re:", "body": "x", "to": "a@b.com"},
    }
    out = rc.evaluate_chase(
        reply_draft_fact=fact,
        reply_draft_captured_at="2026-06-02T00:00:00+00:00",
        inbound_message_id="MSG2",
        inbound_thread_id="TH1",
        event_thread_ids={"TH1"},
        now=dt.datetime(2026, 6, 3, 12, 0, tzinfo=dt.timezone.utc),
    )
    assert out["recommended_action"] == "regenerate"
    assert out["prior_pending_draft"] is True
    assert out["prior_source_message_id"] == "MSG1"
    assert out["stale_hours"] == 36.0


def test_techjoyce_top_level_anchors_regenerate(bridge_pkg):
    rc = bridge_pkg.reply_chase
    fact = {
        "thread_id": "19e81ff6def3b65f",
        "in_reply_to": "19e84b2d4cf91067",
        "decision": "pending",
        "draft": {
            "subject": "Re: collab",
            "body": "Thanks!",
            "to": "ankush@sparkmedia.la",
        },
    }
    out = rc.evaluate_chase(
        reply_draft_fact=fact,
        reply_draft_captured_at="2026-06-02T02:16:05+00:00",
        inbound_message_id="19e8ef255f17f7af",
        inbound_thread_id="19e81ff6def3b65f",
        event_thread_ids={"19e81ff6def3b65f"},
    )
    assert out["recommended_action"] == "regenerate"


def test_regenerate_on_approved_unsent_chase(bridge_pkg):
    rc = bridge_pkg.reply_chase
    fact = {
        "decision": "approved",
        "source_message_id": "MSG1",
        "gmail_draft": {"draft_id": "d1", "thread_id": "TH1"},
        "draft": {"thread_id": "TH1", "subject": "Re:", "body": "x", "to": "a@b.com"},
    }
    out = rc.evaluate_chase(
        reply_draft_fact=fact,
        reply_draft_captured_at=None,
        inbound_message_id="MSG2",
        inbound_thread_id="TH1",
        event_thread_ids={"TH1"},
    )
    assert out["recommended_action"] == "regenerate"
    assert out["prior_approved_unsent"] is True


def test_apply_open_escalation_defer(bridge_pkg):
    rc = bridge_pkg.reply_chase
    evaluation = {
        "recommended_action": "regenerate",
        "prior_pending_draft": True,
        "prior_source_message_id": "MSG1",
    }
    out = rc.apply_open_escalation_defer(evaluation)
    assert out["recommended_action"] == "defer_escalation"
    assert out["defer_reason"] == "open_escalation_awaiting_answer"
    assert out["deferred_chase_action"] == "regenerate"

    noop = rc.apply_open_escalation_defer({"recommended_action": "proceed_normal"})
    assert noop["recommended_action"] == "proceed_normal"


def test_escalate_thread_fork(bridge_pkg):
    rc = bridge_pkg.reply_chase
    fact = {
        "decision": "pending",
        "source_message_id": "MSG1",
        "draft": {"thread_id": "TH-OLD", "subject": "Re:", "body": "x", "to": "a@b.com"},
    }
    out = rc.evaluate_chase(
        reply_draft_fact=fact,
        reply_draft_captured_at=None,
        inbound_message_id="MSG2",
        inbound_thread_id="TH-NEW",
        event_thread_ids=set(),
    )
    assert out["recommended_action"] == "escalate_thread_fork"


def test_regenerate_cold_outreach_first_gmail_reply(bridge_pkg):
    """LIVE:751 pattern — approved cold-outreach draft, KOL replies on real Gmail thread."""
    rc = bridge_pkg.reply_chase
    fact = {
        "decision": "approved",
        "source_message_id": "draft:outreach_PR0037-20260609_751",
        "gmail_draft": {"draft_id": "d1", "thread_id": "outreach_PR0037-20260609_751"},
        "draft": {
            "thread_id": "outreach_PR0037-20260609_751",
            "subject": "Re: collab",
            "body": "x",
            "to": "designer@example.com",
        },
    }
    out = rc.evaluate_chase(
        reply_draft_fact=fact,
        reply_draft_captured_at="2026-06-10T17:50:00+00:00",
        inbound_message_id="19ebd5c293ec1e41",
        inbound_thread_id="19eb2b8c1a50cc14",
        event_thread_ids=set(),
        now=dt.datetime(2026, 6, 12, 19, 46, tzinfo=dt.timezone.utc),
    )
    assert out["recommended_action"] == "regenerate"
    assert out["prior_approved_unsent"] is True
    assert out["chase_note"] == "cold_outreach_first_gmail_reply"
