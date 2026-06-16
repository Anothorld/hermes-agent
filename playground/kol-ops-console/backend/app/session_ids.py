"""Gateway session_id helpers for kol-ops-console launches."""

from __future__ import annotations


def campaign_draft_session_id(
    env: str,
    campaign_id: str,
    identity_id: int,
) -> str:
    """Per-identity draft session — avoids cross-KOL transcript bloat."""
    return f"kol-campaign-draft:{env}:{campaign_id}:{identity_id}"
