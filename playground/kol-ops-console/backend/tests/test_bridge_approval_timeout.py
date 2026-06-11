"""Bridge client approval timeouts for long-running learning merges."""

from __future__ import annotations

from app.bridge_client import BridgeClient, _LEARNING_APPROVAL_FACT_PATHS


def test_approval_timeout_for_learning_proposals() -> None:
    client = BridgeClient()
    for fact_path in _LEARNING_APPROVAL_FACT_PATHS:
        assert client.approval_timeout_for(fact_path) == client._learning_timeout


def test_approval_timeout_for_reply_draft() -> None:
    client = BridgeClient()
    assert client.approval_timeout_for("approval.reply_draft") == client._approve_timeout


def test_approval_timeout_default_for_other_facts() -> None:
    client = BridgeClient()
    assert client.approval_timeout_for("approval.escalation") == client._default_timeout
