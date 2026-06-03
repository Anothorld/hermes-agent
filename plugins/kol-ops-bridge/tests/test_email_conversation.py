"""Tests for Gmail-based email conversation builder."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_classify_skips_drafts_and_non_sent_outbound(bridge_pkg):
    ec = bridge_pkg.email_conversation
    self_email = "brand@company.com"
    kol_email = "kol@example.com"
    assert ec.classify_gmail_message(
        {"from": "brand@company.com", "labels": ["DRAFT"]},
        self_email=self_email,
        kol_email=kol_email,
    ) is None
    assert ec.classify_gmail_message(
        {"from": "brand@company.com", "labels": ["INBOX"]},
        self_email=self_email,
        kol_email=kol_email,
    ) is None
    assert ec.classify_gmail_message(
        {"from": "brand@company.com", "labels": ["SENT"]},
        self_email=self_email,
        kol_email=kol_email,
    ) == ("outbound", "sent")
    assert ec.classify_gmail_message(
        {"from": "kol@example.com", "labels": ["INBOX"]},
        self_email=self_email,
        kol_email=kol_email,
    ) == ("inbound", "received")


def test_build_email_conversation_merges_threads_and_sorts(bridge_pkg, monkeypatch):
    ec = bridge_pkg.email_conversation
    monkeypatch.setattr(ec.cal, "get_identity", lambda _iid: {"primary_email": "kol@example.com"})
    monkeypatch.setattr(
        ec.cal,
        "latest_facts_for",
        lambda **_k: {"offer.gmail_thread_id": "TH1"},
    )
    monkeypatch.setattr(ec.cal, "list_events", lambda **_k: [])

    client = MagicMock()
    client.is_available.return_value = True
    client.get_profile_email.return_value = "brand@company.com"
    client.get_thread.return_value = [
        {
            "id": "m1",
            "from": "brand@company.com",
            "to": "kol@example.com",
            "subject": "Hi",
            "date": "Mon, 2 Jun 2026 10:00:00 +0000",
            "body": "Hello",
            "labels": ["SENT"],
        },
        {
            "id": "m2",
            "from": "kol@example.com",
            "to": "brand@company.com",
            "subject": "Re: Hi",
            "date": "Tue, 3 Jun 2026 11:00:00 +0000",
            "body": "Thanks",
            "labels": ["INBOX"],
        },
        {
            "id": "m3",
            "from": "brand@company.com",
            "to": "kol@example.com",
            "subject": "Re: Hi",
            "date": "Wed, 4 Jun 2026 12:00:00 +0000",
            "body": "Draft text",
            "labels": ["DRAFT"],
        },
    ]

    out = ec.build_email_conversation(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        client=client,
    )
    assert out["count"] == 2
    assert out["messages"][0]["direction"] == "outbound"
    assert out["messages"][1]["direction"] == "inbound"
    assert all(m.get("source") == "gmail" for m in out["messages"])
