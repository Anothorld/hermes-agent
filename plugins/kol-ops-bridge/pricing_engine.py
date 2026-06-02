"""Deterministic compensation-offer engine (formerly the `kol-pricing-strategist` skill).

This module turns a structured pricing situation into a structured pricing
recommendation **without an LLM**. It was extracted from the
``kol-pricing-strategist`` SKILL so the numbers are reproducible, testable,
and immune to model drift — the parent ``kol-compensation-negotiator`` skill
keeps owning the email wording, but the number, the bounds, and the
human-gate decision come from here.

Pure data in, pure data out: no DB, no HTTP, no file IO. See
``compute_offer`` for the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Optional

PAYING_MODES = frozenset({"paid", "commission", "hybrid"})
NON_PAYING_MODES = frozenset({"gifted", "gifted_no_product"})
_VALID_MODES = frozenset({"gifted", "paid", "commission", "hybrid"})

# Default ratios (documented constants rather than magic numbers inline).
DEFAULT_PAID_RATIO_OF_QUOTE = 0.55
DEFAULT_PAID_RATIO_OF_CEILING_NO_QUOTE = 0.5
# Never counter at exactly the ceiling — the KOL would learn the cap.
MAX_COUNTER_FRACTION_OF_CEILING = 0.65
LOWER_BOUND_FRACTION_OF_CEILING = 0.4
DEFAULT_COUNTER_INCREMENT = 100

_AGENCY_ROLES = frozenset({"agency", "manager", "assistant", "rep", "representative"})
_CREATOR_TIERS = frozenset({"koc", "mid_tier", "top_tier"})
_TIER_ALIASES = {
    "micro": "koc",
    "nano": "koc",
    "small": "koc",
    "koc": "koc",
    "mid": "mid_tier",
    "mid-tier": "mid_tier",
    "mid_tier": "mid_tier",
    "middle": "mid_tier",
    "waist": "mid_tier",
    "腰部": "mid_tier",
    "top": "top_tier",
    "top-tier": "top_tier",
    "top_tier": "top_tier",
    "head": "top_tier",
    "大v": "top_tier",
    "头部": "top_tier",
}
_HOLDING_LINE = (
    "Let me align internally on the campaign economics and come back with a "
    "clear answer, usually within 1-2 business days."
)
_DIRECT_KOL_RATE_REQUEST_LINE = (
    "Totally understand that a cash fee is important for your work. Our first "
    "path for this campaign is still product-led: we would send the piece at "
    "no cost for you to experience and create around. If you do need a cash "
    "supplement on top of the gifted product, could you share the leanest "
    "number that would feel workable for the agreed scope? I'll review it "
    "against this campaign and come back with a clear answer."
)


@dataclass(frozen=True)
class CreatorTierProfile:
    """Negotiation knobs for audience-size based creator tiers."""

    tier: str
    first_quote_ratio: float
    max_quote_ratio: float
    first_increment: int
    second_increment: int
    final_increment: int


_TIER_PROFILES: dict[str, CreatorTierProfile] = {
    "koc": CreatorTierProfile("koc", 0.50, 0.70, 120, 60, 30),
    "mid_tier": CreatorTierProfile("mid_tier", 0.55, 0.75, 150, 70, 30),
    "top_tier": CreatorTierProfile("top_tier", 0.60, 0.80, 180, 80, 40),
}


class PricingInputError(ValueError):
    """Raised when the inbound pricing payload is structurally unusable."""


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    rounded = round(float(value), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _round_cash_counter(value: float) -> int:
    """Round cash offers down to natural negotiation anchors."""
    if value <= 0:
        return 0
    if value < 10:
        return max(1, math.floor(float(value)))
    step = 100 if value >= 100 else 10
    rounded = math.floor(float(value) / step) * step
    return max(step, rounded)


def _round_precise_cash_counter(value: float) -> int:
    """Round down to a specific-looking cash anchor instead of a round hundred."""
    if value <= 0:
        return 0
    if value < 10:
        return max(1, math.floor(float(value)))
    if value < 100:
        return max(10, math.floor(float(value) / 10) * 10)
    rounded = math.floor(float(value) / 10) * 10
    # Exact hundreds read like arbitrary headroom; nudge to a harder anchor.
    if rounded % 100 == 0:
        rounded -= 20
    return max(10, rounded)


def _money(value: Optional[float]) -> str:
    """Format money without unnecessary decimals."""
    rounded = _round(value)
    return "" if rounded is None else str(rounded)


def _gate(reason: str, *, mode: str, currency: str, wording: str = _HOLDING_LINE,
          rationale: str = "", negotiation_phase: Optional[str] = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "mode_decided": mode,
        "target_number": None,
        "target_basis": None,
        "target_currency": currency,
        "lower_bound": None,
        "upper_bound": None,
        "suggested_wording": wording,
        "requires_human_gate": True,
        "gate_reason": reason,
        "rationale_one_line": rationale or reason,
        "negotiation_phase": negotiation_phase,
    }
    return out


def _resolve_contact_type(payload: Mapping[str, Any]) -> str:
    """Return ``direct`` or ``agency`` for negotiation policy branching."""
    explicit = (payload.get("contact_type") or "").strip().lower()
    if explicit in _AGENCY_ROLES or explicit == "agency":
        return "agency"
    if explicit in {"direct", "kol"}:
        return "direct"

    identity = payload.get("identity") or {}
    role = (identity.get("contact_role") or "").strip().lower()
    if role in _AGENCY_ROLES:
        return "agency"

    integrity = (payload.get("identity_integrity") or "").strip().lower()
    if integrity == "delegated":
        return "agency"
    return "direct"


def _normalise_band(band: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Accept both ``{min_pct,max_pct}`` and ``{min,max}`` band shapes."""
    lo = band.get("min_pct", band.get("min"))
    hi = band.get("max_pct", band.get("max"))

    def _pct(v: Any) -> Optional[float]:
        if v is None:
            return None
        v = float(v)
        return v * 100 if v < 1 else v

    return _pct(lo), _pct(hi)


def _mapping_values(*items: Any) -> list[Mapping[str, Any]]:
    """Return mapping-like objects from a mixed payload list."""
    out: list[Mapping[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            out.append(item)
    return out


def _nested_payloads(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidate = payload.get("candidate")
    candidate_payload = candidate.get("payload") if isinstance(candidate, Mapping) else None
    reusable = payload.get("reusable_facts")
    reusable_facts = reusable.get("facts") if isinstance(reusable, Mapping) else None
    return _mapping_values(
        payload,
        payload.get("identity_facts"),
        reusable,
        reusable_facts,
        candidate,
        candidate_payload,
    )


def _normalise_creator_tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    tier = str(value).strip().lower().replace(" ", "_")
    if tier in _CREATOR_TIERS:
        return tier
    return _TIER_ALIASES.get(tier)


def _parse_followers(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    text = str(value).strip().lower().replace(",", "")
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1]
    elif text.endswith("k"):
        multiplier = 1000.0
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000.0
        text = text[:-1]
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    return int(float(match.group(0)) * multiplier)


def _resolve_creator_tier(payload: Mapping[str, Any]) -> Optional[CreatorTierProfile]:
    """Resolve KOC / mid / top tier from explicit labels or follower counts."""
    tier_keys = ("creator_tier", "kol_tier", "tier", "identity.creator_tier", "identity.kol_tier")
    follower_keys = (
        "follower_count", "followers", "fans_count", "fan_count",
        "identity.follower_count", "identity.followers", "identity.fans_count",
    )
    follower_count: Optional[int] = None
    for source in _nested_payloads(payload):
        for key in tier_keys:
            tier = _normalise_creator_tier(source.get(key))
            if tier:
                return _TIER_PROFILES[tier]
        for key in follower_keys:
            parsed = _parse_followers(source.get(key))
            if parsed is not None:
                follower_count = parsed
                break
        if follower_count is not None:
            break

    if follower_count is None:
        return None
    if follower_count < 50_000:
        return _TIER_PROFILES["koc"]
    if follower_count <= 300_000:
        return _TIER_PROFILES["mid_tier"]
    return _TIER_PROFILES["top_tier"]


def compute_offer(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compute a compensation recommendation from a structured situation.

    Args:
        payload: ``{mode, kol_quoted_amount?, kol_quoted_currency?,
            kol_quoted_basis?, campaign_config{...}, relationship{...},
            contact_type?, identity?, identity_integrity?,
            barter_attempted?, rate_requested?, paid_hold_sent? (legacy),
            kol_insists_paid?, paid_ratio_override?}``. See the module docstring / the
            ``kol-pricing-strategist`` SKILL for field semantics.

    Returns:
        A dict with the stable output schema (``mode_decided``,
        ``target_number``, ``lower_bound``, ``upper_bound``,
        ``suggested_wording``, ``requires_human_gate``, ``gate_reason``,
        ``rationale_one_line``, ``negotiation_phase`` …). Every key is
        always present; ``null`` is used where a field does not apply.

    Raises:
        PricingInputError: if ``mode`` is missing/invalid.
    """
    mode = (payload.get("mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        raise PricingInputError(f"mode must be one of {sorted(_VALID_MODES)}; got {mode!r}")

    cfg: Mapping[str, Any] = payload.get("campaign_config") or {}
    currency = payload.get("kol_quoted_currency") or "USD"
    quote = payload.get("kol_quoted_amount")
    quote = float(quote) if quote is not None else None
    unit_price = cfg.get("product_unit_price")
    unit_price = float(unit_price) if unit_price is not None else None
    ceiling = cfg.get("paid_ceiling")
    ceiling = float(ceiling) if ceiling is not None else None

    if mode == "gifted":
        return _gifted(currency, unit_price)
    if mode == "paid":
        return _paid(quote, ceiling, unit_price, currency, payload, cfg)
    if mode == "commission":
        return _commission(quote, cfg, currency, payload)
    return _hybrid(quote, unit_price, cfg, currency, payload)


def _gifted(currency: str, unit_price: Optional[float]) -> dict[str, Any]:
    value_hint = ""
    if unit_price is not None:
        value_hint = f" (retail value around {currency} {_money(unit_price)})"
    return {
        "mode_decided": "gifted",
        "target_number": None,
        "target_basis": None,
        "target_currency": currency,
        "lower_bound": None,
        "upper_bound": None,
        "suggested_wording": (
            f"For this campaign, our preferred structure is a product-led "
            f"collaboration: we would send the piece at no cost{value_hint} "
            "so you can experience it first and decide how it naturally fits "
            "your content. We'd keep the brief focused and give you room to "
            "bring your own style to the feature."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": "No quote / no mode signal → barter-only gifted offer.",
        "negotiation_phase": "barter_first",
    }


def _direct_kol_barter_first(
    currency: str,
    unit_price: Optional[float],
    *,
    quote: Optional[float],
) -> dict[str, Any]:
    """Direct KOL paid signal — always try one gifted/barter round first."""
    value_hint = ""
    if unit_price is not None:
        value_hint = f" valued around {currency} {_money(unit_price)}"
    quote_note = ""
    if quote is not None:
        quote_note = (
            " I know you mentioned a cash fee; for this round, I'd like to "
            "separate the product value from the cash line and explore the "
            "product-led structure first."
        )
    return {
        "mode_decided": "gifted",
        "target_number": None,
        "target_basis": None,
        "target_currency": currency,
        "lower_bound": None,
        "upper_bound": None,
        "suggested_wording": (
            f"Thanks for being upfront about compensation. For this campaign, "
            f"I'd love to first explore a product-led setup: the product is"
            f"{value_hint}, the scope is intentionally focused, and we'd send "
            f"it for you to experience before creating. That keeps the "
            f"collaboration centered on authentic product fit instead of a "
            f"cash-heavy placement.{quote_note} Would you be open to trying "
            f"the gifted route for this one?"
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": (
            "Direct KOL + paid signal + no prior barter attempt → gifted-first."
        ),
        "negotiation_phase": "barter_first",
    }


def _direct_kol_rate_request(currency: str) -> dict[str, Any]:
    """Direct KOL insisted on paid after barter — ask them to anchor first."""
    return {
        "mode_decided": "paid",
        "target_number": None,
        "target_basis": None,
        "target_currency": currency,
        "lower_bound": None,
        "upper_bound": None,
        "suggested_wording": _DIRECT_KOL_RATE_REQUEST_LINE,
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": (
            "Direct KOL insisted paid after barter attempt → request KOL's rate first."
        ),
        "negotiation_phase": "rate_request",
    }


def _direct_kol_rate_followup(currency: str) -> dict[str, Any]:
    """Direct KOL still has not provided a cash number after rate request."""
    return {
        "mode_decided": "paid",
        "target_number": None,
        "target_basis": None,
        "target_currency": currency,
        "lower_bound": None,
        "upper_bound": None,
        "suggested_wording": (
            "To keep this moving, could you send over the leanest cash "
            "supplement that would work for you on top of the gifted product? "
            "Once I have your number, I can assess it properly for this "
            "campaign instead of guessing from our side."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": (
            "Direct KOL has not provided a cash quote after rate request → nudge."
        ),
        "negotiation_phase": "rate_request",
    }


def _is_high_quote(quote: Optional[float], ceiling: float) -> bool:
    """True when the KOL anchor needs internal-review wording."""
    if quote is None:
        return False
    return quote > ceiling or quote >= ceiling * MAX_COUNTER_FRACTION_OF_CEILING


def _product_gift_clause(currency: str, unit_price: Optional[float]) -> str:
    """Phrase anchoring that the product is gifted separately from cash."""
    if unit_price is not None:
        return (
            f"we'll still send the product at no cost (retail value around "
            f"{currency} {_money(unit_price)})"
        )
    return "we'll still send the product at no cost"


def _cash_supplement_clause(currency: str, target: float) -> str:
    """Name the counter explicitly as cash on top of the gifted product."""
    return (
        f"the workable cash supplement on top of the gifted product is "
        f"{currency} {_money(target)}"
    )


def _tier_context_clause(tier: Optional[CreatorTierProfile]) -> str:
    if tier is None:
        return "single-campaign economics"
    if tier.tier == "koc":
        return "this first KOC test and product-value exchange"
    if tier.tier == "mid_tier":
        return "the vertical creator benchmark and one-off test ROI"
    return "the schedule-sensitive top-creator test and controlled revision scope"


def _first_single_test_counter_wording(
    *,
    contact: str,
    currency: str,
    target: float,
    unit_price: Optional[float],
) -> str:
    """First paid counter — single-test intro budget + future campaign priority."""
    amount = f"{currency} {_money(target)}"
    gift_prefix = ""
    if unit_price is not None:
        gift_prefix = (
            f"Separately from the cash line, we'll send the product at no cost "
            f"(retail value around {currency} {_money(unit_price)}). "
        )
    if contact == "agency":
        return (
            f"{gift_prefix}Because this is our first single-campaign category test, "
            f"leadership has locked the one-time intro budget tightly — the approved "
            f"cash supplement ceiling for this round is only {amount}. "
            f"We'd like to advance this internally on our side. If conversion from "
            f"this single test meets baseline, you'll be prioritized for future "
            f"multi-campaign collaborations on our slate. Can we proceed at {amount}?"
        )
    return (
        f"{gift_prefix}Because this is our first single-campaign test together, "
        f"my manager has locked the category's one-time intro budget very tightly — "
        f"the ceiling for this round is only {amount}. I genuinely want "
        f"to help get this partnership approved on our side. If the conversion "
        f"results from this single test meet our baseline, we'll absolutely "
        f"prioritize you for future multi-campaign collaborations with us. Would you be "
        f"open to supporting us on this first round and moving forward at {amount}?"
    )


def _concession_clause(tier: Optional[CreatorTierProfile], prior_offer: Optional[float]) -> str:
    if prior_offer is None:
        if tier and tier.tier == "mid_tier":
            return (
                "The number is based on our internal benchmark for a single "
                "new-customer test, so we need to start disciplined."
            )
        if tier and tier.tier == "top_tier":
            return (
                "The cash line is calibrated for a clean first test with a "
                "controlled revision loop and fast approvals."
            )
        return (
            "The number is based on our internal benchmark for this campaign, "
            "so we need to keep the first test disciplined."
        )
    return (
        "I rechecked the budget internally and only have a very small move "
        "available on the cash line; the process efficiency is the main lever "
        "we can offer here."
    )


def _paid_counter_wording(
    *,
    contact: str,
    currency: str,
    target: float,
    quote: Optional[float],
    high_quote: bool,
    unit_price: Optional[float],
    tier: Optional[CreatorTierProfile],
    prior_offer: Optional[float],
) -> str:
    if prior_offer is None:
        return _first_single_test_counter_wording(
            contact=contact,
            currency=currency,
            target=target,
            unit_price=unit_price,
        )

    gift = _product_gift_clause(currency, unit_price)
    cash = _cash_supplement_clause(currency, target)
    tier_context = _tier_context_clause(tier)
    concession = _concession_clause(tier, prior_offer)
    if high_quote:
        if contact == "agency":
            return (
                "Thanks for sharing the cash rate. I reviewed it against the "
                "campaign economics, and we need to keep the incremental cash "
                "fee tighter because the product value is already included in "
                f"the package. For {tier_context}, {gift}; {cash}. {concession} "
                "If that is workable, we can keep the scope clean, limit the "
                "revision loop, and move quickly on timing."
            )
        return (
            "Thanks for sharing your cash rate. I really like the idea of "
            f"finding a way to make this work, but for this round we need to "
            f"separate the product value from the cash line: {gift}. Given "
            f"{tier_context} and the focused scope, {cash}. {concession} If that feels doable, "
            "we'll keep the brief light, the process simple, and the timeline "
            "easy on your side."
        )
    if contact == "agency":
        return (
            "Thanks for sharing the cash rate. Since the product is included "
            f"separately at no cost, we are evaluating only the incremental "
            f"cash line here. For {tier_context}, {gift}; {cash}. {concession} If that is in "
            "range, we can keep the deliverables and review process efficient "
            "for both sides."
        )
    return (
        "Thanks for sharing your cash rate. I do want to make this feel fair "
        f"while still keeping the campaign lean. The product itself is a "
        f"meaningful part of the value exchange — {gift}. For the cash portion, "
        f"{cash}. {concession} If that feels workable, we can keep the brief clean and make "
        "the collaboration smooth on your side."
    )


def _tiered_increment(
    *,
    prior_offer: float,
    lower: float,
    ceiling: float,
    tier: Optional[CreatorTierProfile],
    cfg: Mapping[str, Any],
) -> float:
    """Return a shrinking concession after the first cash counter."""
    if cfg.get("paid_counter_increment") is not None and tier is None:
        return float(cfg["paid_counter_increment"])
    profile = tier or CreatorTierProfile(
        "default", DEFAULT_PAID_RATIO_OF_QUOTE, 0.75, DEFAULT_COUNTER_INCREMENT, 50, 25,
    )
    first_band = lower + profile.first_increment
    second_band = first_band + profile.second_increment
    if prior_offer < first_band:
        return float(profile.first_increment)
    if prior_offer < second_band:
        return float(profile.second_increment)
    return float(profile.final_increment)


def _resolve_cash_target(
    quote: Optional[float],
    ceiling: float,
    cfg: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> float:
    """Compute the cash supplement counter (product is always gifted separately)."""
    override = payload.get("paid_ratio_override")
    if override is None:
        override = cfg.get("paid_ratio_override")
    tier = _resolve_creator_tier(payload)
    prior_offer = _prior_cash_offer(payload)
    uses_tier_strategy = tier is not None
    if override is not None:
        ratio = float(override)
        request = quote
        if request is None:
            req_raw = payload.get("kol_latest_requested_amount")
            if req_raw is None and isinstance(payload.get("campaign_facts"), Mapping):
                req_raw = payload["campaign_facts"].get("offer.latest_requested_amount")
            request = float(req_raw) if req_raw is not None else None
        if request is not None and 0 < ratio <= 1:
            target = request * ratio
        else:
            target = ratio
        capped = min(target, ceiling * MAX_COUNTER_FRACTION_OF_CEILING)
        return float(_round_precise_cash_counter(capped))
    else:
        target_floor = cfg.get("paid_target_budget")
        if target_floor is None:
            target_floor = ceiling * LOWER_BOUND_FRACTION_OF_CEILING
        target = float(target_floor)

        if prior_offer is not None:
            increment = _tiered_increment(
                prior_offer=prior_offer, lower=target, ceiling=ceiling, tier=tier, cfg=cfg,
            )
            target = max(target, prior_offer + increment)
        elif tier is not None and quote is not None:
            target = max(target, quote * tier.first_quote_ratio)

    if quote is not None:
        quote_ratio = tier.first_quote_ratio if tier else DEFAULT_PAID_RATIO_OF_QUOTE
        quote_cap = quote * quote_ratio if prior_offer is None else quote * (
            tier.max_quote_ratio if tier else DEFAULT_PAID_RATIO_OF_QUOTE
        )
    else:
        quote_cap = ceiling * DEFAULT_PAID_RATIO_OF_CEILING_NO_QUOTE
    capped = min(target, quote_cap, ceiling * MAX_COUNTER_FRACTION_OF_CEILING)
    if uses_tier_strategy or payload.get("precise_cash_anchor"):
        return float(_round_precise_cash_counter(capped))
    return float(_round_cash_counter(capped))


def _prior_cash_offer(payload: Mapping[str, Any]) -> Optional[float]:
    """Return the latest cash amount we already proposed, if supplied."""
    for key in ("prior_proposed_amount", "proposed_amount", "offer.proposed_amount"):
        value = payload.get(key)
        if value is not None:
            return float(value)
    campaign_facts = payload.get("campaign_facts")
    if isinstance(campaign_facts, Mapping):
        value = campaign_facts.get("offer.proposed_amount")
        if value is not None:
            return float(value)
    return None


def _paid_counter(
    quote: Optional[float],
    ceiling: float,
    unit_price: Optional[float],
    currency: str,
    payload: Mapping[str, Any],
    cfg: Mapping[str, Any],
    *,
    contact: str,
) -> dict[str, Any]:
    """Compute a cash-supplement counter; product gifting is separate."""
    tier = _resolve_creator_tier(payload)
    prior_offer = _prior_cash_offer(payload)
    target = _resolve_cash_target(quote, ceiling, cfg, payload)
    high_quote = _is_high_quote(quote, ceiling)
    wording = _paid_counter_wording(
        contact=contact,
        currency=currency,
        target=target,
        quote=quote,
        high_quote=high_quote,
        unit_price=unit_price,
        tier=tier,
        prior_offer=prior_offer,
    )
    mode = "hybrid" if unit_price is not None else "paid"
    target_budget = cfg.get("paid_target_budget")
    lower = target_budget if target_budget is not None else ceiling * LOWER_BOUND_FRACTION_OF_CEILING
    lower = min(float(lower), ceiling)

    return {
        "mode_decided": mode,
        "target_number": _round(target),
        "target_basis": payload.get("kol_quoted_basis") or "flat",
        "target_currency": currency,
        "lower_bound": _round(lower),
        "upper_bound": _round(ceiling),
        "suggested_wording": wording,
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": (
            f"Cash supplement counter at {_round(target)} "
            f"(creator_tier={tier.tier if tier else 'unknown'}; "
            f"product gifted separately; cash ceiling {ceiling})."
        ),
        "negotiation_phase": "paid_counter",
    }


def _paid(
    quote: Optional[float],
    ceiling: Optional[float],
    unit_price: Optional[float],
    currency: str,
    payload: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    if ceiling is None:
        return _gate(
            "missing_paid_ceiling",
            mode="paid",
            currency=currency,
            rationale="paid mode but campaign_config.paid_ceiling is unset",
        )

    contact = _resolve_contact_type(payload)
    barter_attempted = bool(payload.get("barter_attempted"))
    rate_requested = bool(payload.get("rate_requested") or payload.get("paid_hold_sent"))
    kol_insists_paid = bool(payload.get("kol_insists_paid"))

    if contact == "direct" and not barter_attempted:
        return _direct_kol_barter_first(currency, unit_price, quote=quote)

    if contact == "direct" and barter_attempted and quote is None:
        if not rate_requested and kol_insists_paid:
            return _direct_kol_rate_request(currency)
        if not rate_requested:
            return _direct_kol_rate_request(currency)
        if rate_requested:
            # Already asked for their cash number — wait/nudge, do not counter.
            return _direct_kol_rate_followup(currency)

    return _paid_counter(
        quote, ceiling, unit_price, currency, payload, cfg, contact=contact,
    )


def _commission(
    quote: Optional[float],
    cfg: Mapping[str, Any],
    currency: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    contact = _resolve_contact_type(payload)
    barter_attempted = bool(payload.get("barter_attempted"))
    if contact == "direct" and not barter_attempted:
        unit_price = cfg.get("product_unit_price")
        unit_price = float(unit_price) if unit_price is not None else None
        return _direct_kol_barter_first(currency, unit_price, quote=quote)

    band = cfg.get("commission_band") or {}
    lo, hi = _normalise_band(band)
    if lo is None or hi is None:
        return _gate(
            "missing_commission_band",
            mode="commission",
            currency=currency,
            rationale="commission mode but campaign_config.commission_band is unset",
        )
    cookie = band.get("cookie_days")
    attribution = band.get("attribution")
    extras = []
    if cookie:
        extras.append(f"{cookie}-day cookie")
    if attribution:
        extras.append(f"{attribution} attribution")
    extras_str = (" (" + ", ".join(extras) + ")") if extras else ""
    if quote is not None and quote > hi:
        return {
            "mode_decided": "commission",
            "target_number": _round(hi),
            "target_basis": "percent",
            "target_currency": currency,
            "lower_bound": _round(lo),
            "upper_bound": _round(hi),
            "suggested_wording": (
                f"Our commission structure for this campaign tops out at "
                f"{_round(hi)}%{extras_str}. To keep creator economics "
                "consistent across the program, we need to stay inside that "
                "range rather than create a one-off exception."
            ),
            "requires_human_gate": False,
            "gate_reason": None,
            "rationale_one_line": f"KOL asked {quote}% > max {hi}% → counter at max.",
            "negotiation_phase": "paid_counter",
        }
    target = quote if quote is not None else hi
    if contact == "agency" and quote is not None and quote > lo:
        target = max(lo, min(quote * 0.9, hi))
    return {
        "mode_decided": "commission",
        "target_number": _round(target),
        "target_basis": "percent",
        "target_currency": currency,
        "lower_bound": _round(lo),
        "upper_bound": _round(hi),
        "suggested_wording": (
            f"We can structure this at {_round(target)}% commission"
            f"{extras_str}. That keeps the campaign economics consistent "
            "while still giving you upside if the content performs."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": f"Commission {_round(target)}% within band [{lo},{hi}].",
        "negotiation_phase": "paid_counter",
    }


def _hybrid(
    quote: Optional[float],
    unit_price: Optional[float],
    cfg: Mapping[str, Any],
    currency: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Hybrid mode = gifted product + cash supplement; same path as ``paid``."""
    contact = _resolve_contact_type(payload)
    barter_attempted = bool(payload.get("barter_attempted"))
    if contact == "direct" and not barter_attempted:
        return _direct_kol_barter_first(currency, unit_price, quote=quote)

    ceiling = cfg.get("paid_ceiling")
    ceiling = float(ceiling) if ceiling is not None else None
    return _paid(quote, ceiling, unit_price, currency, payload, cfg)


__all__ = ["compute_offer", "PricingInputError", "PAYING_MODES", "NON_PAYING_MODES"]
