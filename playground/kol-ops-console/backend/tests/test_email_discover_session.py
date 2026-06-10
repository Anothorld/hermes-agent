"""Tests for per-run email-discover session_id (browser tab isolation)."""

from __future__ import annotations

from app.email_discover_dispatch import email_discover_session_id


def test_email_discover_session_id_includes_run_token() -> None:
    sid_a = email_discover_session_id("LIVE", 42, "run-aaa")
    sid_b = email_discover_session_id("LIVE", 42, "run-bbb")
    assert sid_a == "kol-email-discover:LIVE:42:run-aaa"
    assert sid_b == "kol-email-discover:LIVE:42:run-bbb"
    assert sid_a != sid_b


def test_email_discover_session_ids_unique_across_identities() -> None:
    sid_a = email_discover_session_id("TEST", 1, "tok-1")
    sid_b = email_discover_session_id("TEST", 2, "tok-2")
    assert sid_a != sid_b
