"""Deterministic per-turn lane routing (from `kol-reply-dispatcher` Steps 4-5).

After ``kol-reply-dispatcher`` writes the classifier facts and re-fetches the
server goal_state, it must pick exactly one *primary* lane/skill and demote
the others to ``approval.pending_topics`` side-topics. That decision is
deterministic — it is a lookup over the goal→skill table plus a fixed lane
priority with a severity-reversal rule — so it lives here rather than being
re-derived by the model each turn (which risks picking the wrong child skill
or silently dropping a non-primary lane).

Pure: no DB, no HTTP. ``select_next_skill`` consumes the server goal_state
snapshot + classifier signals and returns the routing decision.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

# Default lane priority (commerce wins ties).
LANE_PRIORITY = ("commerce", "fulfillment", "publish", "meta")

# Goal → child skill. ``logistics`` and ``content_production`` are
# fact-conditional and resolved in ``_skill_for_goal``.
GOAL_SKILL: dict[str, str] = {
    "outreach": "kol-cold-outreach",  # reengagement resolved via meta.path
    "interest_qualification": "kol-interest-qualifier",
    "product_selection": "kol-product-selector",
    "deliverables_scope": "kol-deliverables-clarifier",
    "compensation_negotiation": "kol-compensation-negotiator",
    "contract_signing": "kol-contract-coordinator",
    "payout_setup": "kol-payout-method-intake",
    "content_review_and_golive": "kol-content-reviewer",
    "post_collab_archival": "kol-archival-writer",
}

# Severity-bearing signal name → lane it escalates. ``escalation_pattern_match``
# and any signal carrying an explicit ``lane`` are handled dynamically.
SEVERITY_SIGNAL_LANE: dict[str, str] = {
    "not_received": "fulfillment",
    "address_questioned": "fulfillment",
    "package_lost": "fulfillment",
    "package_damaged": "fulfillment",
    "rejects_revisions": "publish",
    "content_dispute": "publish",
}
_CRITICAL_SEVERITIES = frozenset({"critical", "blocking"})


def _present(facts: Mapping[str, Any], key: str) -> bool:
    val = facts.get(key)
    if val is None:
        return False
    if isinstance(val, (str, list, dict, tuple)) and len(val) == 0:
        return False
    return True


def _skill_for_goal(goal: str, facts: Mapping[str, Any],
                    meta: Mapping[str, Any]) -> Optional[str]:
    """Resolve the child skill, handling the fact-conditional goals."""
    if goal == "outreach":
        path = meta.get("path") or facts.get("meta.path")
        return "kol-reengagement-outreach" if path == "reengagement" else "kol-cold-outreach"
    if goal == "logistics":
        if not _present(facts, "fulfillment.address_collected"):
            return "kol-shipping-intake"
        return "kol-logistics-tracker"
    if goal == "content_production":
        if not _present(facts, "offer.brief_sent"):
            return "kol-brief-sender"
        # brief sent but no draft yet → nothing to draft, wait for the KOL.
        return None
    return GOAL_SKILL.get(goal)


def _severity_lanes(signals: Iterable[Mapping[str, Any]]) -> set[str]:
    """Return lanes that carry a critical/blocking signal."""
    lanes: set[str] = set()
    for sig in signals or []:
        if not isinstance(sig, Mapping):
            continue
        severity = (sig.get("severity") or "").lower()
        if severity not in _CRITICAL_SEVERITIES:
            continue
        name = sig.get("name") or ""
        lane = sig.get("lane")
        if lane in LANE_PRIORITY:
            lanes.add(lane)
        elif name in SEVERITY_SIGNAL_LANE:
            lanes.add(SEVERITY_SIGNAL_LANE[name])
        elif name.startswith("escalation_pattern_match") and sig.get("lane"):
            lanes.add(sig["lane"])
    return lanes


def _lane_action(goal_row: Mapping[str, Any], facts: Mapping[str, Any],
                 meta: Mapping[str, Any]) -> dict[str, Any]:
    """Map one goal_state row to a lane action."""
    status = goal_row.get("status")
    goal = goal_row.get("goal") or goal_row.get("name")
    if status in {"satisfied", "skipped", "aborted", "inactive", None}:
        return {"action": "idle", "status": status, "goal": goal, "skill": None}
    if goal_row.get("blocking_escalation_id"):
        return {"action": "idle", "status": "blocked", "goal": goal, "skill": None,
                "reason": "blocking_escalation_open"}
    if goal_row.get("human_gates") or goal_row.get("gates_triggered"):
        return {"action": "escalate", "status": status, "goal": goal, "skill": None,
                "reason": "human_gate_triggered"}
    if status == "blocked":
        return {"action": "idle", "status": "blocked", "goal": goal, "skill": None}
    skill = _skill_for_goal(goal, facts, meta)
    if skill is None:
        return {"action": "wait", "status": status, "goal": goal, "skill": None,
                "reason": "no_child_skill_yet"}
    return {"action": "draft", "status": status, "goal": goal, "skill": skill}


def select_next_skill(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the primary lane/skill and demote the rest to side-topics.

    Args:
        payload: ``{"goals": {<goal>: {status, lane, blocking_escalation_id,
            human_gates?}}, "facts": {<ns.key>: val}, "signals":
            [{name, severity, lane?}], "meta": {path?}}``. ``goals`` is the
            server goal_state (authoritative); ``facts`` are the latest facts
            used to resolve fact-conditional goals.

    Returns:
        ``{"primary_lane", "primary_goal", "primary_skill", "lane_actions",
        "side_topics", "severity_reversal_applied"}``. ``primary_skill`` is
        ``None`` when no lane has an actionable draft (all idle / waiting /
        escalating).
    """
    goals: Mapping[str, Any] = payload.get("goals") or {}
    facts: Mapping[str, Any] = payload.get("facts") or {}
    meta: Mapping[str, Any] = payload.get("meta") or {}
    signals = payload.get("signals") or []

    # Build per-lane action by walking goals; keep the first actionable goal
    # per lane (goal_state typically exposes one active goal per lane).
    lane_actions: dict[str, dict[str, Any]] = {}
    for goal_name, row in goals.items():
        row = dict(row)
        row.setdefault("goal", goal_name)
        lane = row.get("lane")
        if lane not in LANE_PRIORITY:
            continue
        action = _lane_action(row, facts, meta)
        # An actionable lane (draft/escalate/wait) supersedes an idle one.
        prior = lane_actions.get(lane)
        if prior is None or (prior["action"] == "idle" and action["action"] != "idle"):
            lane_actions[lane] = action

    severity_lanes = _severity_lanes(signals)
    draftable = [ln for ln in LANE_PRIORITY
                 if lane_actions.get(ln, {}).get("action") == "draft"]

    primary_lane: Optional[str] = None
    reversal = False
    # Severity reversal: a critical fulfillment/publish lane outranks commerce.
    severe_draftable = [ln for ln in draftable if ln in severity_lanes and ln != "commerce"]
    if severe_draftable:
        primary_lane = min(severe_draftable, key=LANE_PRIORITY.index)
        reversal = True
    elif draftable:
        primary_lane = min(draftable, key=LANE_PRIORITY.index)

    side_topics: list[str] = []
    for lane in LANE_PRIORITY:
        act = lane_actions.get(lane)
        if not act or lane == primary_lane:
            continue
        if act["action"] in {"draft", "escalate", "wait"}:
            topic = act.get("reason") or act["action"]
            side_topics.append(f"{lane}:{act.get('goal')}:{topic}")

    primary = lane_actions.get(primary_lane) if primary_lane else None
    return {
        "primary_lane": primary_lane,
        "primary_goal": primary.get("goal") if primary else None,
        "primary_skill": primary.get("skill") if primary else None,
        "lane_actions": lane_actions,
        "side_topics": side_topics,
        "severity_reversal_applied": reversal,
    }


__all__ = ["select_next_skill", "GOAL_SKILL", "LANE_PRIORITY"]
