"""Tests for operator mailbox discovery and email resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_resolve_mailbox_email_keeps_real_address(bridge_pkg):
    gc = bridge_pkg.gmail_console
    client = MagicMock()
    client.get_profile_email.return_value = "should-not-be-called@example.com"
    assert gc.resolve_mailbox_email(client, "candice@povison-collab.com") == (
        "candice@povison-collab.com"
    )
    client.get_profile_email.assert_not_called()


@pytest.mark.parametrize(
    "raw_label",
    ["legacy", "", "legacy-user-1@imported.local"],
)
def test_resolve_mailbox_email_uses_profile_for_placeholders(bridge_pkg, raw_label):
    gc = bridge_pkg.gmail_console
    client = MagicMock()
    client.get_profile_email.return_value = "candice@povison-collab.com"
    assert gc.resolve_mailbox_email(client, raw_label) == "candice@povison-collab.com"


def test_list_operator_gmail_clients_resolves_legacy_fallback(bridge_pkg, monkeypatch):
    gc = bridge_pkg.gmail_console
    client = MagicMock()
    client.is_available.return_value = True
    client.get_profile_email.return_value = "candice@povison-collab.com"

    monkeypatch.setattr(gc, "fetch_gmail_connections", lambda: [])
    monkeypatch.setattr(gc, "_local_token_mailboxes", lambda: [])
    monkeypatch.setattr(
        bridge_pkg.gmail_credentials,
        "legacy_token_path",
        lambda: __import__("pathlib").Path("/tmp/legacy.json"),
    )

    class _LegacyClient:
        @staticmethod
        def __call__(*_a, **_k):
            return client

    monkeypatch.setattr(gc, "GmailClient", lambda credentials_path=None: client)

    mailboxes = gc.list_operator_gmail_clients(force_refresh=True)
    assert len(mailboxes) == 1
    assert mailboxes[0].google_email == "candice@povison-collab.com"
    assert mailboxes[0].user_id == 0


def test_console_base_defaults_to_console_port(bridge_pkg, monkeypatch):
    gc = bridge_pkg.gmail_console
    monkeypatch.delenv("KOC_CONSOLE_BASE", raising=False)
    assert gc._console_base() == "http://127.0.0.1:8765"
