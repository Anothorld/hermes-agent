"""Tests for inbound reply processor failure paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from kol_ops_bridge_pkg.gmail_client import GmailMessage, GmailUnavailable
from kol_ops_bridge_pkg.inbound_reply.deps import InboundDeps, MatchBridgeError
from kol_ops_bridge_pkg.inbound_reply.matcher import match_identity
from kol_ops_bridge_pkg.inbound_reply.processor import handle_mailbox_mismatch, process_message
from kol_ops_bridge_pkg.inbound_reply.schemas import IdentityMatch


def _msg(**kwargs: Any) -> GmailMessage:
    defaults = {
        "message_id": "msg-in",
        "thread_id": "thread-1",
        "from_addr": "kol@example.com",
        "to": "ops@brand.com",
        "cc": "",
        "subject": "Re: Collab",
        "snippet": "yes",
        "body": "Sounds good",
        "date": "Mon, 1 Jun 2026 10:00:00 +0000",
        "in_reply_to": "out-msg-1",
        "references": "",
    }
    defaults.update(kwargs)
    return GmailMessage(**defaults)


def _matched(**kwargs: Any) -> IdentityMatch:
    defaults = {
        "identity_id": 42,
        "campaign_id": "C1",
        "thread_integrity": "strict",
        "matched_by": "in_reply_to",
        "history_thread_id": "thread-1",
        "identity_integrity": "matched",
        "reasons": [],
        "content_risk": "c1",
        "risk_controls": {
            "allow_autoflow": True,
            "gate_budget": False,
            "gate_contract": False,
            "gate_payout": False,
        },
        "sender_email": "kol@example.com",
        "expected_email": "kol@example.com",
    }
    defaults.update(kwargs)
    return IdentityMatch(**defaults)


@dataclass
class _FakeBridge:
    events: list[dict[str, Any]] = field(default_factory=list)
    identities: dict[int, dict[str, Any]] = field(default_factory=dict)
    dispatch_status: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    list_events_error: Optional[Exception] = None
    write_error: Optional[Exception] = None
    dispatch_status_error: Optional[Exception] = None

    def list_recent_events(self, *, env: str, limit: int) -> list[dict[str, Any]]:
        if self.list_events_error:
            raise self.list_events_error
        return self.events[:limit]

    def find_events_for_inbound_match(
        self,
        *,
        env: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        sender_email: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        del env, limit, thread_id, in_reply_to, sender_email
        return []

    def get_identity(self, identity_id: int) -> dict[str, Any] | None:
        return self.identities.get(identity_id)

    def get_facts(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        return self.facts

    def reply_dispatch_status(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        env: str,
    ) -> dict[str, Any]:
        if self.dispatch_status_error:
            raise self.dispatch_status_error
        return self.dispatch_status

    def write_inbound_event(self, body: dict[str, Any]) -> None:
        if self.write_error:
            raise self.write_error

    def dispatch_context(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        return {"identity_id": identity_id, "campaign_id": campaign_id}

    def reply_chase_hint(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        thread_id: str | None,
        env: str,
    ) -> dict[str, Any]:
        return {"recommended_action": "proceed_normal", "prior_pending_draft": False}


def test_match_identity_bridge_error_raises(bridge_pkg):
    bridge = _FakeBridge(list_events_error=RuntimeError("bridge down"))
    with pytest.raises(MatchBridgeError):
        match_identity(_msg(), env="TEST", bridge=bridge)


def test_process_message_gateway_failure_returns_retry(bridge_pkg, monkeypatch):
    bridge = _FakeBridge(
        events=[
            {
                "env": "TEST",
                "identity_id": 42,
                "campaign_id": "C1",
                "event_type": "outbound_sent",
                "payload": {"message_id": "out-msg-1", "thread_id": "thread-1"},
            }
        ],
        identities={42: {"primary_email": "kol@example.com"}},
    )
    gateway = MagicMock()
    gateway.run.return_value = None
    client = MagicMock()
    client.get_thread.return_value = []
    deps = InboundDeps(bridge=bridge, gateway=gateway)

    status = process_message(
        _msg(),
        "TEST",
        client=client,
        deps=deps,
    )
    assert status == "retry"
    bridge.write_inbound_event  # noqa: B018 — ensure write happened via no raise


def test_process_message_retry_gateway_only_skips_rewrite(bridge_pkg, monkeypatch):
    bridge = _FakeBridge(
        events=[
            {
                "env": "TEST",
                "identity_id": 42,
                "campaign_id": "C1",
                "event_type": "outbound_sent",
                "payload": {"message_id": "out-msg-1", "thread_id": "thread-1"},
            }
        ],
        identities={42: {"primary_email": "kol@example.com"}},
        dispatch_status={"should_retry_gateway_only": True},
    )
    gateway = MagicMock()
    gateway.run.return_value = "run-123"
    client = MagicMock()
    client.get_thread.return_value = []
    deps = InboundDeps(bridge=bridge, gateway=gateway)
    write_mock = MagicMock(side_effect=AssertionError("should not rewrite event"))
    bridge.write_inbound_event = write_mock  # type: ignore[method-assign]

    status = process_message(_msg(), "TEST", client=client, deps=deps)
    assert status == "dispatched"
    write_mock.assert_not_called()


def test_handle_mailbox_mismatch_escalation_failure_returns_retry(bridge_pkg, monkeypatch):
    from kol_ops_bridge_pkg.inbound_reply import processor as processor_mod

    monkeypatch.setattr(
        processor_mod,
        "ensure_mailbox_mismatch_escalation",
        MagicMock(side_effect=RuntimeError("escalation db locked")),
    )
    outcome = handle_mailbox_mismatch(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        msg=_msg(),
        mailbox_email="other@brand.com",
        mismatch={
            "mailbox_mismatch": True,
            "bound_mailbox_email": "ops@brand.com",
            "detected_mailbox_email": "other@brand.com",
        },
    )
    assert outcome == "retry"


def test_process_message_thread_fetch_failure_still_dispatches(bridge_pkg):
    bridge = _FakeBridge(
        events=[
            {
                "env": "TEST",
                "identity_id": 42,
                "campaign_id": "C1",
                "event_type": "outbound_sent",
                "payload": {"message_id": "out-msg-1", "thread_id": "thread-1"},
            }
        ],
        identities={42: {"primary_email": "kol@example.com"}},
    )
    gateway = MagicMock()
    gateway.run.return_value = "run-456"
    client = MagicMock()
    client.get_thread.side_effect = GmailUnavailable("rate limit")
    deps = InboundDeps(bridge=bridge, gateway=gateway)

    status = process_message(_msg(), "TEST", client=client, deps=deps)
    assert status == "dispatched"
    gateway.run.assert_called_once()
