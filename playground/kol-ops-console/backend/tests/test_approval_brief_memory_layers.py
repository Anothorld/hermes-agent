"""Approval / redraft brief shape for memory-layer wiring."""

from __future__ import annotations

from app.routers.campaigns import (
    _compose_approval_brief,
    _compose_redraft_brief,
)


def test_approval_brief_seeds_before_selected_kols_and_no_duplicate_contract():
    brief = _compose_approval_brief(
        campaign_id="CID-1",
        env="LIVE",
        selected_rows=[
            {"identity_id": 1, "handle": "@a"},
            {"identity_id": 2, "handle": "@b"},
        ],
        actor_email="ops@example.com",
        actor_user_id=1,
        test_mode_to=None,
    )
    seed_pos = brief.index("# hindsight_recall_seed")
    kols_pos = brief.index("# selected_kols")
    assert seed_pos < kols_pos
    assert brief.count("# hindsight_recall_seed") == 2
    assert "Bridge agent hard rules" not in brief
    assert "Bridge runtime contract is in system instructions" in brief


def test_redraft_brief_seed_first_and_no_duplicate_contract():
    brief = _compose_redraft_brief(
        campaign_id="CID-1",
        env="LIVE",
        identity_id=42,
        handle="@kol",
        actor_email="ops@example.com",
        test_mode_to=None,
        campaign_snapshot={"product_display_name": "Sofa"},
        identity_snapshot={"primary_email": "k@ex.com"},
        dispatch_context_snapshot={"learning_hints": {"hints": []}},
    )
    header_pos = brief.index("# campaign_redraft_outreach")
    seed_pos = brief.index("# hindsight_recall_seed")
    scope_pos = brief.index("# scope")
    assert header_pos < seed_pos < scope_pos
    assert "Bridge agent hard rules" not in brief
    assert "cal_snapshot" in brief
