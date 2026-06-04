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
