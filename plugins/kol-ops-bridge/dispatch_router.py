"""Deterministic per-turn lane routing (from `kol-reply-dispatcher` Steps 4-5).

After ``kol-reply-dispatcher`` writes the classifier facts and re-fetches the
server goal_state, it must pick exactly one *primary* lane/skill and demote
the others to ``approval.pending_topics`` side-topics. That decision is
deterministic — it is a lookup over the goal→skill table plus a fixed lane
priority with a severity-reversal rule — so it lives here rather than being
re-derived by the model each turn (which risks picking the wrong child skill
or silently dropping a non-primary lane).

Multi-goal dispatch (``select_draftable_plan``) returns **all** active
draftable goals within the snapshot so fragment-mode child skills can run in
parallel and a synthesizer can merge their outputs into one reply.

Pure: no DB, no HTTP. ``select_next_skill`` / ``select_draftable_plan`` consume
the server goal_state snapshot + classifier signals and return routing decisions.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from .schema import GOAL_NAMES, LANES

# Default lane priority (commerce wins ties).
LANE_PRIORITY = LANES

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

# Fact keys each goal's fragment-mode child skill may propose (disjoint by design).
GOAL_OWNED_FACTS: dict[str, tuple[str, ...]] = {
    "outreach": ("offer.outreach_sent",),
    "interest_qualification": (
        # ``offer.interest_signal`` is classifier-only (confirmed/declined);
        # fragment must not pre-commit interest — see interest-qualifier SKILL.
        "offer.interest_clarify_asked",
        "offer.interest_clarify_question",
        "identity.contact_role",
        "identity.manager_name",
        "identity.manager_email",
    ),
    "product_selection": (
        "offer.sku_locked",
        "offer.color_or_variant_locked",
        "offer.fit_confirmed",
        "offer.sku_requested",
        "offer.proposed_skus",
    ),
    "deliverables_scope": (
        # Fragment / clarify turns use *_proposed until classifier confirms on a
        # later inbound; committed keys satisfy goal_state — do not propose them
        # from fragment mode before KOL agreement (see deliverables-clarifier SKILL).
        "offer.deliverable_platforms_proposed",
        "offer.deliverable_count_proposed",
        "offer.usage_rights_discussed",
        "offer.deliverable_count_per_platform_requested",
    ),
    "compensation_negotiation": (
        # ``offer.agreed_terms`` is classifier-only when KOL accepts; fragment
        # may set mode + proposed_* counter-offer but must not satisfy the goal
        # in the same turn (see compensation-negotiator SKILL).
        "offer.compensation_mode",
        "offer.proposed_amount",
        "offer.proposed_basis",
        "offer.proposed_currency",
        "offer.kol_paid_quote",
        "offer.barter_attempted",
        "offer.rate_requested",
        "offer.paid_hold_sent",
    ),
    "contract_signing": (
        "offer.contract_sent",
        "offer.contract_signed",
    ),
    "logistics": (
        "fulfillment.address_collected",
        "fulfillment.shipping_method",
        "fulfillment.tracking_filled",
        "fulfillment.delivered_confirmed",
    ),
    "payout_setup": ("payout.method_collected",),
    "content_production": (
        "offer.brief_sent",
        "offer.draft_submitted",
    ),
    "content_review_and_golive": (
        "offer.review_verdict",
        "offer.posted_url",
        "offer.boost_assets_status",
    ),
    "post_collab_archival": (
        "approval.archival_outcome",
        "approval.relationship_synced",
        "approval.preferred_skus_synced",
        "approval.preferred_mode_synced",
        "approval.followups_pending",
    ),
}

_GOAL_ORDER = {name: idx for idx, name in enumerate(GOAL_NAMES)}
_LANE_ORDER = {name: idx for idx, name in enumerate(LANE_PRIORITY)}

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


class FactOwnershipError(ValueError):
    """Raised when fragment-mode proposed facts overlap or violate ownership."""

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__("; ".join(conflicts))


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
    lane = goal_row.get("lane")
    if status in {"satisfied", "skipped", "aborted", "inactive", None}:
        return {"action": "idle", "status": status, "goal": goal, "lane": lane,
                "skill": None}
    if goal_row.get("blocking_escalation_id"):
        return {"action": "idle", "status": "blocked", "goal": goal, "lane": lane,
                "skill": None, "reason": "blocking_escalation_open"}
    if goal_row.get("human_gates") or goal_row.get("gates_triggered"):
        return {"action": "escalate", "status": status, "goal": goal, "lane": lane,
                "skill": None, "reason": "human_gate_triggered"}
    if status == "blocked":
        return {"action": "idle", "status": "blocked", "goal": goal, "lane": lane,
                "skill": None}
    skill = _skill_for_goal(goal, facts, meta)
    if skill is None:
        return {"action": "wait", "status": status, "goal": goal, "lane": lane,
                "skill": None, "reason": "no_child_skill_yet"}
    return {"action": "draft", "status": status, "goal": goal, "lane": lane,
            "skill": skill}


def _defer_prefers_contract(campaign_cfg: Mapping[str, Any]) -> bool:
    """When defer is on, contract_signing wins over compensation_negotiation."""
    return bool(campaign_cfg.get("defer_terms_to_contract")) and not bool(
        campaign_cfg.get("strict_explicit_accept"),
    )


def resolve_campaign_cfg(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve policy fields for routing from explicit cfg, meta, or defaults."""
    explicit = payload.get("campaign_cfg")
    if isinstance(explicit, Mapping) and explicit:
        return dict(explicit)
    meta = payload.get("meta") or {}
    nested = meta.get("campaign_config") if isinstance(meta, Mapping) else None
    if isinstance(nested, Mapping) and nested:
        return dict(nested)
    return {
        "defer_terms_to_contract": True,
        "strict_explicit_accept": False,
        "implicit_accept_enabled": True,
    }


def _collapse_lane_actions(
    all_actions: list[dict[str, Any]],
    campaign_cfg: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Pick one action row per lane (commerce defer prefers contract_signing)."""
    by_lane: dict[str, list[dict[str, Any]]] = {}
    for act in all_actions:
        lane = act.get("lane")
        if lane in LANE_PRIORITY:
            by_lane.setdefault(lane, []).append(act)

    lane_actions: dict[str, dict[str, Any]] = {}
    for lane in LANE_PRIORITY:
        cands = by_lane.get(lane, [])
        if not cands:
            continue
        if lane == "commerce" and _defer_prefers_contract(campaign_cfg):
            contract = next(
                (
                    a
                    for a in cands
                    if a.get("goal") == "contract_signing" and a["action"] == "draft"
                ),
                None,
            )
            if contract:
                lane_actions[lane] = contract
                continue
        for act in cands:
            prior = lane_actions.get(lane)
            if prior is None or (prior["action"] == "idle" and act["action"] != "idle"):
                lane_actions[lane] = act
    return lane_actions


def _sort_goal_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(row: dict[str, Any]) -> tuple[int, int]:
        lane = row.get("lane") or "meta"
        goal = row.get("goal") or ""
        return (_LANE_ORDER.get(lane, 99), _GOAL_ORDER.get(goal, 99))

    return sorted(actions, key=_key)


def _build_goal_actions(
    goals: Mapping[str, Any],
    facts: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return one action row per goal in the payload (not collapsed per lane)."""
    out: list[dict[str, Any]] = []
    for goal_name, row in goals.items():
        row = dict(row)
        row.setdefault("goal", goal_name)
        lane = row.get("lane")
        if lane not in LANE_PRIORITY:
            continue
        action = _lane_action(row, facts, meta)
        out.append(action)
    return _sort_goal_actions(out)


def assert_disjoint(proposed_by_goal: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Merge fragment-mode proposed facts after ownership / overlap checks.

    Args:
        proposed_by_goal: ``{goal_name: {fact_key: value, ...}, ...}``.

    Returns:
        Flat merged fact dict ready for ``write-facts-multi`` namespaces.

    Raises:
        FactOwnershipError: duplicate keys or keys outside the goal's ownership set.
    """
    merged: dict[str, Any] = {}
    conflicts: list[str] = []
    for goal, facts in proposed_by_goal.items():
        if not isinstance(facts, Mapping):
            continue
        allowed = set(GOAL_OWNED_FACTS.get(goal, ()))
        if not allowed:
            conflicts.append(f"{goal}: no GOAL_OWNED_FACTS entry")
            continue
        for key, val in facts.items():
            if key in merged:
                conflicts.append(f"{key}: proposed by multiple goals")
            elif key not in allowed:
                conflicts.append(f"{key}: not owned by goal {goal}")
            else:
                merged[key] = val
    if conflicts:
        raise FactOwnershipError(conflicts)
    return merged


def select_draftable_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return all draftable goals for multi-fragment dispatch + synthesis.

    Args:
        payload: Same shape as ``select_next_skill``. Optional keys:
            ``lane_filter`` — when set, only return draftable goals in that lane.

    Returns:
        ``{"draftable", "escalate", "wait", "idle", "primary_contributor",
        "lane_actions", "severity_reversal_applied"}``. ``draftable`` lists
        every goal with ``action=="draft"`` (including multiple goals in the
        same lane). ``primary_contributor`` is the first draftable row after
        lane+goal ordering (highest priority).
    """
    goals: Mapping[str, Any] = payload.get("goals") or {}
    facts: Mapping[str, Any] = payload.get("facts") or {}
    meta: Mapping[str, Any] = payload.get("meta") or {}
    signals = payload.get("signals") or []
    lane_filter = payload.get("lane_filter")
    campaign_cfg = resolve_campaign_cfg(payload)

    all_actions = _build_goal_actions(goals, facts, meta)
    draftable = [a for a in all_actions if a["action"] == "draft"]
    escalate = [a for a in all_actions if a["action"] == "escalate"]
    wait = [a for a in all_actions if a["action"] == "wait"]
    idle = [a for a in all_actions if a["action"] == "idle"]

    if lane_filter:
        draftable = [a for a in draftable if a.get("lane") == lane_filter]
        escalate = [a for a in escalate if a.get("lane") == lane_filter]

    severity_lanes = _severity_lanes(signals)
    reversal = bool(severity_lanes & {a.get("lane") for a in draftable} - {"commerce"})

    lane_actions = _collapse_lane_actions(all_actions, campaign_cfg)

    primary_contributor = draftable[0] if draftable else None
    if _defer_prefers_contract(campaign_cfg):
        contract = next(
            (a for a in draftable if a.get("goal") == "contract_signing"),
            None,
        )
        if contract:
            primary_contributor = contract
    return {
        "draftable": draftable,
        "escalate": escalate,
        "wait": wait,
        "idle": idle,
        "primary_contributor": primary_contributor,
        "lane_actions": lane_actions,
        "severity_reversal_applied": reversal,
    }


def select_next_skill(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the primary lane/skill and demote the rest to side-topics.

    Args:
        payload: ``{"goals": {...}, "facts": {...}, "signals": [...],
            "meta": {...}, "campaign_cfg": {...}?}``. Optional
            ``campaign_cfg.defer_terms_to_contract`` makes
            ``contract_signing`` win over ``compensation_negotiation`` on
            the commerce lane when both are draftable.

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
    campaign_cfg = resolve_campaign_cfg(payload)

    all_actions = _build_goal_actions(goals, facts, meta)
    lane_actions = _collapse_lane_actions(all_actions, campaign_cfg)

    severity_lanes = _severity_lanes(signals)
    draftable_lanes = [ln for ln in LANE_PRIORITY
                       if lane_actions.get(ln, {}).get("action") == "draft"]

    primary_lane: Optional[str] = None
    reversal = False
    severe_draftable = [ln for ln in draftable_lanes if ln in severity_lanes and ln != "commerce"]
    if severe_draftable:
        primary_lane = min(severe_draftable, key=LANE_PRIORITY.index)
        reversal = True
    elif draftable_lanes:
        primary_lane = min(draftable_lanes, key=LANE_PRIORITY.index)

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


__all__ = [
    "select_next_skill",
    "select_draftable_plan",
    "assert_disjoint",
    "resolve_campaign_cfg",
    "FactOwnershipError",
    "GOAL_SKILL",
    "GOAL_OWNED_FACTS",
    "LANE_PRIORITY",
]
