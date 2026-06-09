"""Tests for global-seen recovery and gateway retry backoff."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

from kol_ops_bridge_pkg.gmail_client import GmailMessage
from kol_ops_bridge_pkg.inbound_reply.recovery import needs_reprocess_after_global_seen
from kol_ops_bridge_pkg.inbound_reply.state import (
    clear_retry_backoff,
    record_retry_backoff,
    retry_not_before,
)


@dataclass
class _FakeBridge:
    events: list[dict[str, Any]] = field(default_factory=list)
    identities: dict[int, dict[str, Any]] = field(default_factory=dict)
    dispatch_status: dict[str, Any] = field(default_factory=dict)

    def list_recent_events(self, *, env: str, limit: int) -> list[dict[str, Any]]:
        return self.events[:limit]

    def get_identity(self, identity_id: int) -> dict[str, Any] | None:
        return self.identities.get(identity_id)

    def reply_dispatch_status(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        env: str,
    ) -> dict[str, Any]:
        return self.dispatch_status


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


def test_needs_reprocess_when_retry_gateway_only(bridge_pkg):
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
    assert needs_reprocess_after_global_seen(_msg(), env="TEST", bridge=bridge) is True


def test_no_reprocess_when_skip_poller(bridge_pkg):
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
        dispatch_status={"should_skip_poller": True},
    )
    assert needs_reprocess_after_global_seen(_msg(), env="TEST", bridge=bridge) is False


def test_retry_backoff_exponential(bridge_pkg):
    state: dict[str, Any] = {}
    record_retry_backoff(state, env="TEST", message_id="m1")
    first = retry_not_before(state, env="TEST", message_id="m1")
    assert first > time.time()
    record_retry_backoff(state, env="TEST", message_id="m1")
    second = retry_not_before(state, env="TEST", message_id="m1")
    assert second >= first
    clear_retry_backoff(state, env="TEST", message_id="m1")
    assert retry_not_before(state, env="TEST", message_id="m1") == 0.0
