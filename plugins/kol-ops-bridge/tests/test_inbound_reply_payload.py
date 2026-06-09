"""Tests for inbound reply payload helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from kol_ops_bridge_pkg.gmail_client import GmailUnavailable
from kol_ops_bridge_pkg.inbound_reply.payload import build_thread_history


def test_build_thread_history_degrades_on_gmail_error(bridge_pkg):
    client = MagicMock()
    client.get_thread.side_effect = GmailUnavailable("quota")
    history = build_thread_history(
        client=client,
        thread_id="thread-1",
        latest_message_id="msg-1",
    )
    assert history == []
