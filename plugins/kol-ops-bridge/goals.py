"""Goal definitions for the v2.4 goal-driven KOL pipeline.

Each `Goal` describes one slice of the deal-progression state machine.

The core abstraction is intentionally minimal:

* ``required_facts`` — list of fully-qualified fact keys (e.g.
  ``offer.compensation_mode``) that must be present (truthy) to mark
  the goal satisfied.
* ``entry_predicate`` — function ``(state, ctx) -> bool`` deciding
  whether the goal can be activated from the current state.
* ``human_gates`` — list of ``(name, predicate)`` pairs; each
  predicate that returns ``True`` raises a human-approval gate.
* ``lane`` — which parallel pipeline lane this goal belongs to.

Special cases (``contract_signing`` may auto-skip when
``campaign_cfg.contract_required`` is false; ``logistics`` may auto-skip
for pure-commission/no-product flows; ``outreach`` distinguishes cold
vs reengagement via ``meta.path``) are encoded as goal-specific
predicates rather than a separate machinery.

The dataclasses here are pure data + pure functions. Persistence is
the sole responsibility of ``cal.recompute_goals``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .schema import GOAL_NAMES, LANES  # noqa: F401  re-export for callers

# A "state" is a flat dict keyed by fully-qualified fact key
# (``<namespace>.<dotted_path>``) → value. ``None`` / empty string /
# empty list are treated as "missing".
State = Mapping[str, Any]


@dataclass(frozen=True)
class Context:
    """Inputs the goal predicates may inspect besides ``State``."""

    campaign_cfg: Mapping[str, Any] = field(default_factory=dict)
    relationship: Mapping[str, Any] = field(default_factory=dict)
    is_repeat_kol: bool = False


def _present(state: State, key: str) -> bool:
    val = state.get(key)
    if val is None:
        return False
    if isinstance(val, (list, tuple, dict, str)) and len(val) == 0:
        return False
    return True


@dataclass(frozen=True)
class Gate:
    name: str
    triggered: bool
    detail: str = ""


@dataclass(frozen=True)
class Goal:
    name: str
    lane: str
    required_facts: Sequence[str]
    entry: Callable[[State, Context], bool] = field(default=lambda s, c: True)
    skip_when: Callable[[State, Context], bool] = field(default=lambda s, c: False)
    gate_predicates: Sequence[Callable[[State, Context], Gate]] = field(default_factory=tuple)

    # ---- core protocol -------------------------------------------------

    def missing(self, state: State) -> list[str]:
        return [k for k in self.required_facts if not _present(state, k)]

    def is_satisfied(self, state: State) -> bool:
        return not self.missing(state)

    def can_enter(self, state: State, ctx: Context) -> bool:
        return self.entry(state, ctx)

    def is_skipped(self, state: State, ctx: Context) -> bool:
        return self.skip_when(state, ctx)

    def human_gates_triggered(self, state: State, ctx: Context) -> list[Gate]:
        return [g for g in (p(state, ctx) for p in self.gate_predicates) if g.triggered]


# ---------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------


def _interest_confirmed(s: State, _c: Context) -> bool:
    return s.get("offer.interest_signal") == "confirmed"


def _product_locked(s: State, _c: Context) -> bool:
    return _present(s, "offer.sku_locked")


def _deliverables_set(s: State, _c: Context) -> bool:
    return _present(s, "offer.deliverable_platforms")


def _compensation_satisfied(s: State, _c: Context) -> bool:
    return _present(s, "offer.compensation_mode") and _present(s, "offer.agreed_terms")


def _contract_satisfied(s: State, c: Context) -> bool:
    if not c.campaign_cfg.get("contract_required", True):
        return True
    return _present(s, "offer.contract_signed")


_PAYING_MODES = frozenset({"paid", "commission", "hybrid"})
_NON_PAYING_MODES = frozenset({"gifted", "gifted_no_product"})


def _payout_required(s: State, c: Context) -> bool:
    return _contract_satisfied(s, c) and s.get("offer.compensation_mode") in _PAYING_MODES


def _outreach_sent(s: State, _c: Context) -> bool:
    return _present(s, "offer.outreach_sent")


def _content_review_done(s: State, _c: Context) -> bool:
    return s.get("offer.review_verdict") == "approved"


def _aborted(s: State, _c: Context) -> bool:
    return s.get("offer.interest_signal") == "declined" or s.get("meta.aborted") is True


# ---------- gate factories ----------


def _gate_repeat_disputed(_s: State, c: Context) -> Gate:
    last = (c.relationship or {}).get("last_outcome")
    triggered = c.is_repeat_kol and last in {"disputed", "content_failed"}
    return Gate("reengagement_with_disputed_history", triggered, f"last_outcome={last}")


def _gate_sku_off_whitelist(s: State, c: Context) -> Gate:
    sku = s.get("offer.sku_requested")
    wl = c.campaign_cfg.get("sku_whitelist") or []
    triggered = bool(sku) and bool(wl) and sku not in wl
    return Gate("sku_off_whitelist", triggered, f"requested={sku}")


def _gate_deliverables_over_cap(s: State, c: Context) -> Gate:
    cap = c.campaign_cfg.get("deliverable_count_per_platform")
    requested = s.get("offer.deliverable_count_per_platform_requested")
    triggered = cap is not None and requested is not None and requested > cap
    return Gate("deliverables_over_cap", triggered, f"requested={requested} cap={cap}")


def _gate_compensation_over_ceiling(s: State, c: Context) -> Gate:
    paid = s.get("offer.kol_paid_quote")
    ceiling = c.campaign_cfg.get("paid_ceiling")
    triggered = paid is not None and ceiling is not None and paid > ceiling
    return Gate("paid_over_ceiling", triggered, f"quote={paid} ceiling={ceiling}")


def _gate_contract_change(s: State, _c: Context) -> Gate:
    triggered = bool(s.get("approval.contract_change_request"))
    return Gate("contract_change_request", triggered)


def _gate_logistics_anomaly(s: State, _c: Context) -> Gate:
    triggered = bool(s.get("approval.logistics_anomaly"))
    return Gate("logistics_anomaly", triggered)


def _gate_review_overflow(s: State, c: Context) -> Gate:
    rounds = s.get("offer.review_round_count") or 0
    cap = (c.campaign_cfg.get("audit_thresholds") or {}).get("max_review_rounds", 3)
    triggered = rounds > cap
    return Gate("review_overflow", triggered, f"rounds={rounds} cap={cap}")


# ---------------------------------------------------------------------------
# Goal table
# ---------------------------------------------------------------------------


GOALS: dict[str, Goal] = {
    "outreach": Goal(
        name="outreach",
        lane="commerce",
        required_facts=("offer.outreach_sent",),
        gate_predicates=(_gate_repeat_disputed,),
    ),
    "interest_qualification": Goal(
        name="interest_qualification",
        lane="commerce",
        required_facts=("offer.interest_signal",),
        entry=_outreach_sent,
        skip_when=lambda s, c: s.get("offer.interest_signal") == "declined",
    ),
    "product_selection": Goal(
        name="product_selection",
        lane="commerce",
        required_facts=("offer.sku_locked", "offer.color_or_variant_locked", "offer.fit_confirmed"),
        entry=_interest_confirmed,
        gate_predicates=(_gate_sku_off_whitelist,),
    ),
    "deliverables_scope": Goal(
        name="deliverables_scope",
        lane="commerce",
        required_facts=(
            "offer.deliverable_platforms",
            "offer.deliverable_count_per_platform",
            "offer.usage_rights_discussed",
        ),
        entry=_interest_confirmed,
        gate_predicates=(_gate_deliverables_over_cap,),
    ),
    "compensation_negotiation": Goal(
        name="compensation_negotiation",
        lane="commerce",
        required_facts=("offer.compensation_mode", "offer.agreed_terms"),
        entry=_deliverables_set,
        gate_predicates=(_gate_compensation_over_ceiling,),
    ),
    "contract_signing": Goal(
        name="contract_signing",
        lane="commerce",
        required_facts=("offer.contract_sent", "offer.contract_signed"),
        entry=_compensation_satisfied,
        skip_when=lambda s, c: not c.campaign_cfg.get("contract_required", True),
        gate_predicates=(_gate_contract_change,),
    ),
    "logistics": Goal(
        name="logistics",
        lane="fulfillment",
        required_facts=(
            "fulfillment.address_collected",
            "fulfillment.shipping_method",
            "fulfillment.tracking_filled",
            "fulfillment.delivered_confirmed",
        ),
        entry=_contract_satisfied,
        skip_when=lambda s, c: s.get("offer.compensation_mode") == "commission_no_product",
        gate_predicates=(_gate_logistics_anomaly,),
    ),
    "payout_setup": Goal(
        name="payout_setup",
        lane="fulfillment",
        required_facts=("payout.method_collected",),
        entry=_payout_required,
        skip_when=lambda s, c: s.get("offer.compensation_mode") in _NON_PAYING_MODES,
    ),
    "content_production": Goal(
        name="content_production",
        lane="fulfillment",
        required_facts=("offer.brief_sent", "offer.draft_submitted"),
        entry=lambda s, c: bool(s.get("fulfillment.delivered_confirmed"))
        or (s.get("offer.compensation_mode") == "gifted_no_product" and bool(s.get("offer.brief_sent"))),
    ),
    "content_review_and_golive": Goal(
        name="content_review_and_golive",
        lane="publish",
        required_facts=("offer.review_verdict", "offer.posted_url", "offer.boost_assets_status"),
        entry=lambda s, c: bool(s.get("offer.draft_submitted")),
        gate_predicates=(_gate_review_overflow,),
    ),
    "post_collab_archival": Goal(
        name="post_collab_archival",
        lane="meta",
        required_facts=(
            "approval.archival_outcome",
            "approval.relationship_synced",
            "approval.preferred_skus_synced",
            "approval.preferred_mode_synced",
            "approval.followups_pending",
        ),
        entry=lambda s, c: _content_review_done(s, c) or _aborted(s, c),
    ),
}


def all_goals() -> list[Goal]:
    return [GOALS[name] for name in GOAL_NAMES]
