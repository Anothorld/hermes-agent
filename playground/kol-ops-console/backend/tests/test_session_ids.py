"""Tests for per-identity campaign draft session IDs."""

from __future__ import annotations

from app.session_ids import campaign_draft_session_id


def test_campaign_draft_session_id_includes_identity() -> None:
    sid = campaign_draft_session_id("LIVE", "CID-1", 42)
    assert sid == "kol-campaign-draft:LIVE:CID-1:42"
