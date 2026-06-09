"""Tests for inbound identity matcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from kol_ops_bridge_pkg.gmail_client import GmailMessage
from kol_ops_bridge_pkg.inbound_reply.matcher import match_identity


@dataclass
class _FakeBridge:
    events: list[dict[str, Any]]
    identities: dict[int, dict[str, Any]]

    def list_recent_events(self, *, env: str, limit: int) -> list[dict[str, Any]]:
        return self.events[:limit]

    def get_identity(self, identity_id: int) -> dict[str, Any] | None:
        return self.identities.get(identity_id)


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


def test_strict_in_reply_to_match(bridge_pkg):
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
    matched = match_identity(_msg(), env="TEST", bridge=bridge)
    assert matched is not None
    assert matched.identity_id == 42
    assert matched.thread_integrity == "strict"
    assert matched.matched_by == "in_reply_to"


def test_no_match_when_events_empty(bridge_pkg):
    bridge = _FakeBridge(events=[], identities={})
    assert match_identity(_msg(), env="TEST", bridge=bridge) is None


def test_weak_thread_id_match(bridge_pkg):
    bridge = _FakeBridge(
        events=[
            {
                "env": "TEST",
                "identity_id": 7,
                "campaign_id": "C1",
                "event_type": "outbound_sent",
                "payload": {"thread_id": "thread-1", "message_id": "other-msg"},
            }
        ],
        identities={7: {"primary_email": "kol@example.com"}},
    )
    matched = match_identity(
        _msg(in_reply_to="", thread_id="thread-1"),
        env="TEST",
        bridge=bridge,
    )
    assert matched is not None
    assert matched.thread_integrity == "weak"
    assert matched.matched_by == "thread_id"


def test_detached_heuristic_uses_cal_ts_field(bridge_pkg):
    recent_ts = "2026-06-01T10:00:00+00:00"
    bridge = _FakeBridge(
        events=[
            {
                "env": "TEST",
                "identity_id": 99,
                "campaign_id": "C9",
                "event_type": "outbound_sent",
                "ts": recent_ts,
                "payload": {
                    "to": "kol@example.com",
                    "subject": "Collab",
                    "message_id": "old-out",
                },
            }
        ],
        identities={99: {"primary_email": "kol@example.com"}},
    )
    matched = match_identity(
        _msg(
            in_reply_to="",
            thread_id="unrelated-thread",
            subject="Re: Collab",
        ),
        env="TEST",
        bridge=bridge,
    )
    assert matched is not None
    assert matched.thread_integrity == "detached"
    assert matched.matched_by == "heuristic"


def test_detached_ambiguous_top_score_returns_none(bridge_pkg):
    recent_ts = "2026-06-01T10:00:00+00:00"
    bridge = _FakeBridge(
        events=[
            {
                "env": "TEST",
                "identity_id": 1,
                "campaign_id": "C1",
                "event_type": "outbound_sent",
                "ts": recent_ts,
                "payload": {
                    "to": "kol@example.com",
                    "subject": "Collab",
                    "message_id": "out-1",
                },
            },
            {
                "env": "TEST",
                "identity_id": 2,
                "campaign_id": "C2",
                "event_type": "outbound_sent",
                "ts": recent_ts,
                "payload": {
                    "to": "kol@example.com",
                    "subject": "Collab",
                    "message_id": "out-2",
                },
            },
        ],
        identities={
            1: {"primary_email": "kol@example.com"},
            2: {"primary_email": "kol@example.com"},
        },
    )
    matched = match_identity(
        _msg(
            in_reply_to="",
            thread_id="unrelated-thread",
            subject="Re: Collab",
        ),
        env="TEST",
        bridge=bridge,
    )
    assert matched is None
