"""Tests for Gmail sent-body resolution."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_resolve_sent_body_prefers_last_message_after_draft(bridge_pkg):
    gc = bridge_pkg.gmail_client.GmailClient.__new__(bridge_pkg.gmail_client.GmailClient)
    gc.get_thread = MagicMock(return_value=[
        {"id": "m1", "body": "KOL inbound"},
        {"id": "m2", "body": "Agent draft body"},
        {"id": "m3", "body": "Operator edited final"},
    ])
    gc.get_message = MagicMock()
    body, mid = gc.resolve_sent_body(thread_id="TH1", preferred_message_id="m2")
    assert mid == "m3"
    assert body == "Operator edited final"


def test_resolve_sent_body_falls_back_to_draft_message(bridge_pkg):
    gc = bridge_pkg.gmail_client.GmailClient.__new__(bridge_pkg.gmail_client.GmailClient)
    gc.get_thread = MagicMock(return_value=[])
    gc.get_message = MagicMock(return_value=MagicMock(body="Draft only", message_id="m2"))
    body, mid = gc.resolve_sent_body(thread_id="TH1", preferred_message_id="m2")
    assert mid == "m2"
    assert body == "Draft only"
