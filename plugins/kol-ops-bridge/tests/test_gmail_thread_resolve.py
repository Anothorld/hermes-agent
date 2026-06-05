"""Tests for Gmail thread id verification before draft creation."""

from __future__ import annotations

from unittest.mock import MagicMock

def test_is_plausible_gmail_resource_id_rejects_synthetic(bridge_pkg):
    gmail_thread_resolve = bridge_pkg.gmail_thread_resolve
    assert gmail_thread_resolve.is_plausible_gmail_resource_id(
        "proactive-followup:TEST:42:1700000000",
    ) is False
    assert gmail_thread_resolve.is_plausible_gmail_resource_id("TH1") is False
    assert gmail_thread_resolve.is_plausible_gmail_resource_id("19e81ff6def3b65f") is True


def test_resolve_prefers_existing_thread(bridge_pkg):
    gmail_thread_resolve = bridge_pkg.gmail_thread_resolve
    client = MagicMock()
    client.get_thread.return_value = [{"id": "MSG1", "from": "", "date": "", "body": ""}]
    got = gmail_thread_resolve.resolve_thread_id_for_draft(
        client,
        candidate_thread_id="19e81ff6def3b65f",
        source_message_id=None,
    )
    assert got == "19e81ff6def3b65f"
    client.get_message.assert_not_called()


def test_resolve_message_id_stored_as_thread_id(bridge_pkg):
    """Regression: message_id mistaken for thread_id → resolve via get_message."""
    gmail_thread_resolve = bridge_pkg.gmail_thread_resolve
    client = MagicMock()
    client.get_thread.side_effect = lambda tid: (
        [{"id": "TAIL", "from": "", "date": "", "body": ""}]
        if tid == "19e81ff6def3b65f"
        else []
    )
    msg = MagicMock()
    msg.thread_id = "19e81ff6def3b65f"
    client.get_message.return_value = msg
    got = gmail_thread_resolve.resolve_thread_id_for_draft(
        client,
        candidate_thread_id="19e84b2d4cf91067",
        source_message_id=None,
    )
    assert got == "19e81ff6def3b65f"
    client.get_message.assert_called_once_with("19e84b2d4cf91067")


def test_resolve_from_source_message_when_thread_missing(bridge_pkg):
    gmail_thread_resolve = bridge_pkg.gmail_thread_resolve
    client = MagicMock()
    client.get_thread.side_effect = lambda tid: (
        [{"id": "MSG1", "from": "", "date": "", "body": ""}]
        if tid == "19e81ff6def3b65f"
        else []
    )
    msg = MagicMock()
    msg.thread_id = "19e81ff6def3b65f"
    client.get_message.return_value = msg
    got = gmail_thread_resolve.resolve_thread_id_for_draft(
        client,
        candidate_thread_id=None,
        source_message_id="19e84b2d4cf91067",
    )
    assert got == "19e81ff6def3b65f"


def test_resolve_returns_none_for_synthetic_only(bridge_pkg):
    gmail_thread_resolve = bridge_pkg.gmail_thread_resolve
    client = MagicMock()
    assert (
        gmail_thread_resolve.resolve_thread_id_for_draft(
            client,
            candidate_thread_id="proactive-followup:TEST:1:999",
            source_message_id="proactive-followup:TEST:42:1700000000",
        )
        is None
    )
    client.get_thread.assert_not_called()
    client.get_message.assert_not_called()
