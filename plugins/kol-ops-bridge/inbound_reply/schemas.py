"""Shared types for inbound reply dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

ProcessStatus = Literal["dispatched", "skipped", "retry"]


@dataclass(frozen=True)
class IdentityMatch:
    identity_id: int
    campaign_id: Optional[str]
    thread_integrity: str
    matched_by: str
    history_thread_id: Optional[str]
    identity_integrity: str
    reasons: list[str]
    content_risk: str
    risk_controls: dict[str, bool]
    sender_email: Optional[str]
    expected_email: Optional[str]


@dataclass(frozen=True)
class InboundTickStats:
    matched: int
    skipped: int
    retry: int
    scanned: int
    mailboxes: int
    errors: int = 0
    deferred: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "matched": self.matched,
            "skipped": self.skipped,
            "retry": self.retry,
            "scanned": self.scanned,
            "mailboxes": self.mailboxes,
            "errors": self.errors,
            "deferred": self.deferred,
        }
