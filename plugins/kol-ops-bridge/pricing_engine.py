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

from typing import Any, Mapping, Optional

PAYING_MODES = frozenset({"paid", "commission", "hybrid"})
NON_PAYING_MODES = frozenset({"gifted", "gifted_no_product"})
_VALID_MODES = frozenset({"gifted", "paid", "commission", "hybrid"})

# Default ratios (documented constants rather than magic numbers inline).
DEFAULT_PAID_RATIO_OF_QUOTE = 0.7
DEFAULT_PAID_RATIO_OF_CEILING_NO_QUOTE = 0.6
# Never counter at exactly the ceiling — the KOL would learn the cap.
MAX_COUNTER_FRACTION_OF_CEILING = 0.8
HOLDING_LINE = (
    "Let me check internally and come back to you on this — usually 1-2 "
    "business days."
)


class PricingInputError(ValueError):
    """Raised when the inbound pricing payload is structurally unusable."""


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _gate(reason: str, *, mode: str, currency: str, wording: str = HOLDING_LINE,
          rationale: str = "") -> dict[str, Any]:
    return {
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
    }


def _normalise_band(band: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Accept both ``{min_pct,max_pct}`` and ``{min,max}`` band shapes."""
    lo = band.get("min_pct", band.get("min"))
    hi = band.get("max_pct", band.get("max"))
    # Commission bands are sometimes stored as fractions (0.08) and sometimes
    # as percentages (8.0); normalise everything to percentage points.
    def _pct(v: Any) -> Optional[float]:
        if v is None:
            return None
        v = float(v)
        return v * 100 if v < 1 else v

    return _pct(lo), _pct(hi)


def compute_offer(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compute a compensation recommendation from a structured situation.

    Args:
        payload: ``{mode, kol_quoted_amount?, kol_quoted_currency?,
            kol_quoted_basis?, campaign_config{...}, relationship{...},
            paid_ratio_override?}``. See the module docstring / the
            ``kol-pricing-strategist`` SKILL for field semantics.

    Returns:
        A dict with the stable output schema (``mode_decided``,
        ``target_number``, ``lower_bound``, ``upper_bound``,
        ``suggested_wording``, ``requires_human_gate``, ``gate_reason``,
        ``rationale_one_line`` …). Every key is always present; ``null`` is
        used where a field does not apply.

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
        return _paid(quote, ceiling, unit_price, currency, payload)
    if mode == "commission":
        return _commission(quote, cfg, currency)
    return _hybrid(quote, unit_price, cfg, currency)


def _gifted(currency: str, unit_price: Optional[float]) -> dict[str, Any]:
    return {
        "mode_decided": "gifted",
        "target_number": None,
        "target_basis": None,
        "target_currency": currency,
        "lower_bound": None,
        "upper_bound": None,
        "suggested_wording": (
            "We'd love to gift you the product to feature — no fee, full "
            "creative freedom."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": "No quote / no mode signal → barter-only gifted offer.",
    }


def _paid(quote: Optional[float], ceiling: Optional[float],
          unit_price: Optional[float], currency: str,
          payload: Mapping[str, Any]) -> dict[str, Any]:
    if ceiling is None:
        return _gate("missing_paid_ceiling", mode="paid", currency=currency,
                     rationale="paid mode but campaign_config.paid_ceiling is unset")
    if quote is not None and quote > ceiling:
        return _gate("paid_quote_over_ceiling", mode="paid", currency=currency,
                     rationale=f"KOL quote {quote} > ceiling {ceiling} → human gate")
    # Within ceiling (or no quote): counter below the ceiling.
    override = payload.get("paid_ratio_override")
    if quote is not None and unit_price is not None and quote <= unit_price:
        # Tiny quote — try gifted first, but still surface a paid fallback.
        return {
            "mode_decided": "paid",
            "target_number": _round(quote),
            "target_basis": "flat",
            "target_currency": currency,
            "lower_bound": 0.0,
            "upper_bound": _round(min(quote, ceiling)),
            "suggested_wording": (
                "We usually start with gifting given the product value, but "
                "we can include a small fee to cover your time."
            ),
            "requires_human_gate": False,
            "gate_reason": None,
            "rationale_one_line": (
                f"Quote {quote} <= unit_price {unit_price} → gifted-first, small paid fallback."
            ),
        }
    if override is not None:
        target = float(override)
    elif quote is not None:
        target = min(quote * DEFAULT_PAID_RATIO_OF_QUOTE,
                     ceiling * MAX_COUNTER_FRACTION_OF_CEILING)
    else:
        target = ceiling * DEFAULT_PAID_RATIO_OF_CEILING_NO_QUOTE
    target = min(target, ceiling * MAX_COUNTER_FRACTION_OF_CEILING)
    return {
        "mode_decided": "paid",
        "target_number": _round(target),
        "target_basis": payload.get("kol_quoted_basis") or "flat",
        "target_currency": currency,
        "lower_bound": _round(ceiling * 0.5),
        "upper_bound": _round(ceiling),
        "suggested_wording": (
            "Thanks for sharing your rate. Given the scope on our side we can "
            f"do {currency} {_round(target)} for this collaboration."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": (
            f"Counter at {_round(target)} (<= {MAX_COUNTER_FRACTION_OF_CEILING}x ceiling {ceiling})."
        ),
    }


def _commission(quote: Optional[float], cfg: Mapping[str, Any],
                currency: str) -> dict[str, Any]:
    band = cfg.get("commission_band") or {}
    lo, hi = _normalise_band(band)
    if lo is None or hi is None:
        return _gate("missing_commission_band", mode="commission", currency=currency,
                     rationale="commission mode but campaign_config.commission_band is unset")
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
                f"Our standard commission tops out at {_round(hi)}%{extras_str}. "
                "We'd love to work with you at that rate."
            ),
            "requires_human_gate": False,
            "gate_reason": None,
            "rationale_one_line": f"KOL asked {quote}% > max {hi}% → counter at max.",
        }
    target = quote if quote is not None else hi
    return {
        "mode_decided": "commission",
        "target_number": _round(target),
        "target_basis": "percent",
        "target_currency": currency,
        "lower_bound": _round(lo),
        "upper_bound": _round(hi),
        "suggested_wording": (
            f"We can do {_round(target)}% commission{extras_str}. Happy to get you set up."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": f"Commission {_round(target)}% within band [{lo},{hi}].",
    }


def _hybrid(quote: Optional[float], unit_price: Optional[float],
            cfg: Mapping[str, Any], currency: str) -> dict[str, Any]:
    if unit_price is None:
        return _gate("missing_unit_price", mode="hybrid", currency=currency,
                     rationale="hybrid mode but product_unit_price is unset")
    # Product + small cash supplement, tiered by the cash the KOL is asking for.
    cash = quote if quote is not None else 0.0
    if cash <= unit_price:
        supplement = 0.0
    elif cash <= 2 * unit_price:
        supplement = unit_price * 0.3
    else:
        return _gate("hybrid_cash_over_tier", mode="hybrid", currency=currency,
                     rationale=f"requested cash {cash} > 2x unit_price {unit_price} → human gate")
    return {
        "mode_decided": "hybrid",
        "target_number": _round(supplement),
        "target_basis": "flat",
        "target_currency": currency,
        "lower_bound": 0.0,
        "upper_bound": _round(unit_price * 0.3),
        "suggested_wording": (
            "We can send the product plus a small fee to cover your time."
            if supplement
            else "We'd love to gift the product for this collaboration."
        ),
        "requires_human_gate": False,
        "gate_reason": None,
        "rationale_one_line": f"Hybrid: product + cash supplement {_round(supplement)}.",
    }


__all__ = ["compute_offer", "PricingInputError", "PAYING_MODES", "NON_PAYING_MODES"]
