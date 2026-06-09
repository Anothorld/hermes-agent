"""Build dispatch-context bundle shared by HTTP and in-process bridge paths."""

from __future__ import annotations

from typing import Any

from .. import cal
from .. import learning_store


def active_goal_names(*, identity_id: int, campaign_id: str, env: str) -> list[str]:
    active_statuses = {"active", "blocked", "in_progress", "unsatisfied", "paused"}
    return [
        g["goal"]
        for g in cal.get_goal_state(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        )
        if g.get("status") in active_statuses
    ]


def build_dispatch_context_bundle(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
) -> dict[str, Any]:
    """Same payload as ``GET /identities/{id}/dispatch-context``."""
    active_goals = active_goal_names(
        identity_id=identity_id, campaign_id=campaign_id, env=env,
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        learning_hints = learning_store.build_learning_hints(
            conn, env=env, active_goals=active_goals,
        )
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "goals": cal.get_goal_state(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        "lanes": cal.get_lanes_view(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        "relationship": cal.get_relationship(identity_id),
        "reusable_facts": {
            "identity_id": identity_id,
            "facts": cal.get_reusable_facts(identity_id),
        },
        "learning_hints": learning_hints,
        "campaign_config": cal.get_campaign_config(campaign_id, env=env),
        "campaign_facts": cal.latest_facts_for(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        "candidate": cal.get_candidate_for(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        "identity_facts": cal.latest_facts_for(
            identity_id=identity_id, campaign_id=None, env=env,
        ),
    }
