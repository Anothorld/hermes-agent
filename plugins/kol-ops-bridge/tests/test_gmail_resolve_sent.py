"""Tests for Gmail sent-body resolution."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_resolve_sent_body_prefers_last_message_after_draft(bridge_pkg):
    gc = bridge_pkg.gmail_client.GmailClient.__new__(bridge_pkg.gmail_client.GmailClient)
    gc.get_thread = MagicMock(return_value=[
        {"id": "m1", "body": "KOL inbound"},
        {"id": "m2", "body": "Agent draft body"},
        {"id": "m3", "body": "Operator edited final", "labels": ["SENT"]},
    ])
    gc.get_message = MagicMock()
    body, mid = gc.resolve_sent_body(thread_id="TH1", preferred_message_id="m2")
    assert mid == "m3"
    assert body == "Operator edited final"


def test_resolve_sent_body_skips_bounce_dsn_after_sent(bridge_pkg):
    """Gmail threads mailer-daemon DSN with the outbound send — must not capture bounce."""
    gc = bridge_pkg.gmail_client.GmailClient.__new__(bridge_pkg.gmail_client.GmailClient)
    gc.get_thread = MagicMock(return_value=[
        {
            "id": "m_sent",
            "from": "ops@brand.com",
            "body": "Hi Heidi, We keep coming back to the way you layer warmth.",
            "labels": ["SENT"],
        },
        {
            "id": "m_bounce",
            "from": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
            "body": "** Address not found **\n\nYour message wasn't delivered to kol@x.com",
            "labels": ["INBOX", "CATEGORY_UPDATES"],
        },
    ])
    gc.get_message = MagicMock()
    body, mid = gc.resolve_sent_body(thread_id="TH1", preferred_message_id="m_draft")
    assert mid == "m_sent"
    assert "Address not found" not in body
    assert "Hi Heidi" in body


def test_resolve_sent_body_falls_back_to_draft_message(bridge_pkg):
    gc = bridge_pkg.gmail_client.GmailClient.__new__(bridge_pkg.gmail_client.GmailClient)
    gc.get_thread = MagicMock(return_value=[])
    gc.get_message = MagicMock(return_value=MagicMock(body="Draft only", message_id="m2"))
    body, mid = gc.resolve_sent_body(thread_id="TH1", preferred_message_id="m2")
    assert mid == "m2"
    assert body == "Draft only"
