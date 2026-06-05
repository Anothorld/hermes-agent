"""Unit tests for reply_draft thread anchor extraction."""

from __future__ import annotations


def test_extract_thread_anchors_prefers_draft_fields(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    value = {
        "source_message_id": "MSG1",
        "thread_id": "TOP-TH",
        "in_reply_to": "ALT-MSG",
        "draft": {"thread_id": "DRAFT-TH", "body": "x", "to": "a@b.com", "subject": "Re:"},
    }
    assert reply_draft.extract_thread_anchors(value) == ("DRAFT-TH", "MSG1")


def test_extract_thread_anchors_techjoyce_top_level_shape(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    value = {
        "thread_id": "19e81ff6def3b65f",
        "in_reply_to": "19e84b2d4cf91067",
        "draft": {
            "body": "Thanks!",
            "subject": "Re: collab",
            "to": "ankush@sparkmedia.la",
        },
    }
    assert reply_draft.extract_thread_anchors(value) == (
        "19e81ff6def3b65f",
        "19e84b2d4cf91067",
    )
    assert reply_draft.has_thread_anchor(value) is True


def test_has_thread_anchor_false_when_all_missing(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    assert reply_draft.has_thread_anchor(
        {"draft": {"subject": "Re:", "body": "x", "to": "a@b.com"}},
    ) is False


def test_is_initial_outreach_draft_detects_cold_outreach(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    value = {
        "primary_goal": "outreach",
        "child_skill": "kol-cold-outreach",
        "source_message_id": "draft:outreach_C1_42",
        "draft": {
            "thread_id": "outreach_C1_42",
            "subject": "POVISON collab",
            "body": "<p>Hi</p>",
            "to": "kol@x.com",
        },
    }
    assert reply_draft.is_initial_outreach_draft(
        value, campaign_id="C1", identity_id=42,
    ) is True


def test_is_initial_outreach_draft_false_for_inbound_reply(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    value = {
        "primary_goal": "compensation_negotiation",
        "child_skill": "kol-compensation-negotiator",
        "source_message_id": "19e84b2d4cf91067",
        "draft": {
            "thread_id": "19e81ff6def3b65f",
            "subject": "Re: collab",
            "body": "Thanks",
            "to": "kol@x.com",
        },
    }
    assert reply_draft.is_initial_outreach_draft(value) is False


def test_is_proactive_followup_not_initial_outreach(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    value = {
        "primary_goal": "proactive_followup",
        "child_skill": "kol-proactive-followup",
        "source_message_id": "proactive-followup:TEST:42:1700000000",
        "draft": {
            "kind": "proactive_followup",
            "thread_id": "19e81ff6def3b65f",
            "subject": "Re: collab",
            "body": "Checking in",
            "to": "kol@x.com",
        },
    }
    assert reply_draft.is_proactive_followup_draft(value) is True
    assert reply_draft.is_initial_outreach_draft(value) is False


def test_normalize_proactive_followup_thread_from_facts(bridge_pkg):
    reply_draft = bridge_pkg.reply_draft
    child = {"body": "nudge", "subject": "Re: hi"}
    latest = {"from": "kol@x.com", "subject": "hi"}
    reply_draft.normalize_proactive_followup_thread(
        child,
        latest,
        facts={"offer.gmail_sent_thread_id": "19e81ff6def3b65f"},
        identity_id=42,
        campaign_id="C1",
        env="TEST",
    )
    assert child["thread_id"] == "19e81ff6def3b65f"
    assert latest["thread_id"] == "19e81ff6def3b65f"
