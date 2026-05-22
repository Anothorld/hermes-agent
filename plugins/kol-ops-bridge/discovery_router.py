"""Deterministic Discovery → Outreach routing for ``kol-ops-bridge`` v2.4.

The legacy SKILL.md for ``kol-discovery-to-outreach-router`` performed
three CLI calls in sequence:

1. ``select-candidates`` for new_prospect / repeat_kol candidates.
2. ``write-facts`` per identity to record ``identity.outreach_path``.
3. ``open-escalation`` for ``repeat_kol_needs_review`` candidates.

The classification rule is fully deterministic (relationship_status →
outreach_path table), so we lift it into a single Bridge operation that
the SKILL can call once and consume as JSON. Following the workspace
rule "确定性 CRUD 不交给大模型脑补脚本", this module replaces three
LLM-orchestrated CLI calls with one tool.

Stateless and idempotent: re-running on an already-routed pool only
acts on candidates whose ``candidate_status`` is still ``discovered``.
"""

from __future__ import annotations

from typing import Any, Optional

from . import cal  # type: ignore[import-not-found]


# Outreach paths the router can assign.
OUTREACH_PATH_COLD = "cold"
OUTREACH_PATH_REENGAGEMENT = "reengagement"

# relationship_status → outreach_path. None means "do not auto-route".
_PATH_MAP: dict[str, Optional[str]] = {
    "new_prospect": OUTREACH_PATH_COLD,
    "repeat_kol": OUTREACH_PATH_REENGAGEMENT,
    "repeat_kol_needs_review": None,
    "rejected": None,
}


def route_discovery_pool(
    *,
    campaign_id: str,
    env: str = "LIVE",
    selected_by: str = "agent",
    operator_note: str = "",
) -> dict[str, Any]:
    """Route every candidate currently in ``candidate_status='discovered'``.

    Steps (idempotent):
      1. ``cal.resolve_candidate_relationships`` to fill ``relationship_status``.
      2. Walk the pool. For each candidate with ``candidate_status='discovered'``:
         - new_prospect / repeat_kol → flag for ``select-candidates`` and
           record ``identity.outreach_path``.
         - repeat_kol_needs_review → open one escalation.
         - rejected / unknown → leave alone.
      3. Bulk ``cal.select_candidates_for_outreach`` for the flagged set.
      4. For every selected identity, append a single ``identity.outreach_path``
         fact via ``cal.write_facts``.

    Returns a summary dict with the four buckets and the IDs of any
    escalations opened.
    """
    if env not in ("TEST", "LIVE"):
        raise ValueError(f"env must be TEST or LIVE; got {env!r}")
    if not campaign_id:
        raise ValueError("campaign_id required")

    # Step 1 — refresh relationship_status (idempotent).
    cal.resolve_candidate_relationships(campaign_id=campaign_id, env=env)

    # Step 2 — partition.
    candidates = cal.list_candidates(campaign_id, env=env)
    routed_to_cold: list[int] = []
    routed_to_reengagement: list[int] = []
    needs_review: list[dict[str, Any]] = []
    rejected: list[int] = []
    skipped_already_routed: list[int] = []

    for row in candidates:
        identity_id = row.get("identity_id")
        if not identity_id:
            continue
        if row.get("candidate_status") != "discovered":
            skipped_already_routed.append(int(identity_id))
            continue
        rel = row.get("relationship_status") or "new_prospect"
        path = _PATH_MAP.get(rel, None)
        if path == OUTREACH_PATH_COLD:
            routed_to_cold.append(int(identity_id))
        elif path == OUTREACH_PATH_REENGAGEMENT:
            routed_to_reengagement.append(int(identity_id))
        elif rel == "repeat_kol_needs_review":
            needs_review.append({
                "identity_id": int(identity_id),
                "last_outcome": row.get("last_outcome")
                                or _last_outcome_for(int(identity_id)),
            })
        elif rel == "rejected":
            rejected.append(int(identity_id))
        else:
            # Unknown relationship_status — treat conservatively as needs_review.
            needs_review.append({
                "identity_id": int(identity_id),
                "last_outcome": f"unknown_relationship_status:{rel}",
            })

    # Step 3 — bulk select.
    selected_ids = routed_to_cold + routed_to_reengagement
    if selected_ids:
        cal.select_candidates_for_outreach(
            campaign_id=campaign_id,
            identity_ids=selected_ids,
            selected_by=selected_by,
            env=env,
        )

    # Step 4 — write outreach_path fact per identity.
    for ident in routed_to_cold:
        cal.write_facts(
            identity_id=ident,
            campaign_id=campaign_id,
            namespace="identity",
            facts={"identity.outreach_path": OUTREACH_PATH_COLD},
            source="skill:discovery-to-outreach-router",
            env=env,
        )
    for ident in routed_to_reengagement:
        cal.write_facts(
            identity_id=ident,
            campaign_id=campaign_id,
            namespace="identity",
            facts={"identity.outreach_path": OUTREACH_PATH_REENGAGEMENT},
            source="skill:discovery-to-outreach-router",
            env=env,
        )

    # Step 5 — open escalation for each needs_review candidate.
    needs_review_escalations: list[int] = []
    for entry in needs_review:
        esc_id = cal.open_escalation(
            identity_id=entry["identity_id"],
            campaign_id=campaign_id,
            # Per goals.py, the canonical goal name is ``outreach``.
            # Cold vs reengagement is tracked via ``meta.path`` (see
            # OUTREACH_PATH_REENGAGEMENT above), not a separate goal row.
            goal="outreach",
            reason=f"repeat_kol_needs_review:{entry['last_outcome']}",
            resume_context={"path": "reengagement",
                            "last_outcome": entry["last_outcome"]},
            question_to_operator=(
                operator_note
                or "Prior collab risk surfaced; confirm whether to proceed."
            ),
            severity="normal",
            env=env,
        )
        if esc_id is not None:
            needs_review_escalations.append(int(esc_id))

    return {
        "campaign_id": campaign_id,
        "env": env,
        "routed_to_cold": routed_to_cold,
        "routed_to_reengagement": routed_to_reengagement,
        "needs_review_escalations": needs_review_escalations,
        "rejected": rejected,
        "skipped_already_routed": skipped_already_routed,
    }


def _last_outcome_for(identity_id: int) -> Optional[str]:
    rel = cal.get_relationship(identity_id)
    return rel.get("last_outcome") if rel else None
