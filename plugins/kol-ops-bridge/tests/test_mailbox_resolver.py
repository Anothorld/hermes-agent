"""Tests for campaign mailbox binding and access control."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_bind_mailbox_sticky_on_first_write(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    written: list[dict] = []

    def _write_facts(**kwargs):
        written.append(kwargs)
        return 1

    monkeypatch.setattr(mr.cal, "latest_facts_for", lambda **_k: {})
    monkeypatch.setattr(mr.cal, "write_facts", _write_facts)
    client = MagicMock()
    client.is_available.return_value = True
    client.get_profile_email.return_value = "alice@brand.com"
    monkeypatch.setattr(mr, "client_for_user", lambda _uid: client)

    binding = mr.bind_mailbox(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        operator_user_id=7,
        operator_email="alice@brand.com",
        source="test",
    )
    assert binding.user_id == 7
    assert binding.email == "alice@brand.com"
    assert written
    assert written[0]["facts"][mr.FACT_MAILBOX_USER_ID] == 7


def test_bind_mailbox_rejects_different_operator(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    monkeypatch.setattr(
        mr.cal,
        "latest_facts_for",
        lambda **_k: {
            mr.FACT_MAILBOX_USER_ID: 7,
            mr.FACT_MAILBOX_EMAIL: "alice@brand.com",
        },
    )

    with pytest.raises(mr.MailboxNotOwnerError):
        mr.bind_mailbox(
            identity_id=1,
            campaign_id="C1",
            env="TEST",
            operator_user_id=9,
            operator_email="bob@brand.com",
            source="test",
        )


def test_resolve_for_read_denies_non_owner(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    monkeypatch.setattr(
        mr.cal,
        "latest_facts_for",
        lambda **_k: {
            mr.FACT_MAILBOX_USER_ID: 7,
            mr.FACT_MAILBOX_EMAIL: "alice@brand.com",
        },
    )

    with pytest.raises(mr.MailboxAccessDeniedError) as exc_info:
        mr.resolve_for_read(
            identity_id=1,
            campaign_id="C1",
            env="TEST",
            operator_user_id=9,
        )
    assert exc_info.value.bound_email == "alice@brand.com"


def test_resolve_for_read_requires_operator(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    monkeypatch.setattr(mr.cal, "latest_facts_for", lambda **_k: {})

    with pytest.raises(mr.OperatorRequiredError):
        mr.resolve_for_read(
            identity_id=1,
            campaign_id="C1",
            env="TEST",
            operator_user_id=None,
        )


def test_takeover_denied_for_non_owner_operator(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    monkeypatch.setattr(
        mr.cal,
        "latest_facts_for",
        lambda **_k: {
            mr.FACT_MAILBOX_USER_ID: 7,
            mr.FACT_MAILBOX_EMAIL: "alice@brand.com",
        },
    )

    with pytest.raises(mr.TakeoverNotAllowedError):
        mr.assert_takeover_allowed(
            identity_id=1,
            campaign_id="C1",
            env="TEST",
            new_operator_user_id=9,
            requester_role="operator",
        )


def test_takeover_allowed_for_owner(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    monkeypatch.setattr(
        mr.cal,
        "latest_facts_for",
        lambda **_k: {
            mr.FACT_MAILBOX_USER_ID: 7,
            mr.FACT_MAILBOX_EMAIL: "alice@brand.com",
        },
    )
    mr.assert_takeover_allowed(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        new_operator_user_id=9,
        requester_role="owner",
    )


def test_resolve_for_inbound_prefers_detected_mailbox(bridge_pkg, monkeypatch):
    mr = bridge_pkg.mailbox_resolver
    monkeypatch.setattr(
        mr.cal,
        "latest_facts_for",
        lambda **_k: {
            mr.FACT_MAILBOX_USER_ID: 7,
            mr.FACT_MAILBOX_EMAIL: "alice@brand.com",
        },
    )
    clients: dict[int | None, MagicMock] = {}

    def _client(uid):
        c = MagicMock()
        c.is_available.return_value = True
        clients[uid] = c
        return c

    monkeypatch.setattr(mr, "client_for_user", _client)
    mr.resolve_for_inbound_gmail(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        detected_mailbox_user_id=9,
    )
    assert 9 in clients


def test_draft_owned_by_mailbox_filters_unbound_to_legacy(bridge_pkg, monkeypatch):
    gr = bridge_pkg.gmail_reconcile
    monkeypatch.setattr(gr.mailbox_resolver, "read_binding", lambda **_k: None)
    assert gr._draft_owned_by_mailbox(
        identity_id=1, campaign_id="C1", env="TEST", mailbox_user_id=0
    )
    assert not gr._draft_owned_by_mailbox(
        identity_id=1, campaign_id="C1", env="TEST", mailbox_user_id=7
    )
