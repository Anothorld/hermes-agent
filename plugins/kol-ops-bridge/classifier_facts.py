"""Deterministic guardrails for classifier (Step 3) fact writes.

Fragment-mode child skills cannot write goal-satisfying committed keys; the
classifier still can via ``write-facts-multi`` with ``source=email:<id>``.
This module rewrites or drops premature committed values using the same
signal vocabulary as ``kol-email-stage-classifier`` so vague / inquiry inbound
does not advance ``goal_state`` before the KOL actually agrees.

Pure: no DB, no HTTP.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

_MIN_SIGNAL_CONF = 0.6

_INQUIRY_SIGNALS = frozenset({
    "interest_unclear",
    "asks_deliverables",
    "asks_budget",
    "asks_timeline",
})

_POSITIVE_COMMIT_SIGNALS = frozenset({
    "interest_positive",
    "accepts_terms",
    "continues_without_objection",
})

_PRODUCT_INQUIRY_SIGNALS = frozenset({
    "requests_oos_sku",
    "requests_color_swap",
    "asks_deliverables",
    "asks_budget",
    "interest_unclear",
})

_PRODUCT_LOCK_KEYS = (
    "offer.sku_locked",
    "offer.color_or_variant_locked",
    "offer.fit_confirmed",
)

_DELIVERABLE_REWRITES = (
    ("offer.deliverable_platforms", "offer.deliverable_platforms_proposed"),
    ("offer.deliverable_count_per_platform", "offer.deliverable_count_proposed"),
)


def active_signal_names(
    signals: Iterable[Mapping[str, Any]],
    *,
    min_confidence: float = _MIN_SIGNAL_CONF,
) -> set[str]:
    """Return signal names at or above the classifier confidence floor."""
    names: set[str] = set()
    for item in signals:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        try:
            conf = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf >= min_confidence:
            names.add(name)
    return names


def sanitize_classifier_namespaces(
    namespaces: Mapping[str, Mapping[str, Any]],
    signals: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Rewrite classifier namespaces before persistence.

    Args:
        namespaces: Bridge ``write-facts-multi`` namespace dict.
        signals: Classifier ``signals`` array (same turn).

    Returns:
        ``(sanitized_namespaces, adjustments)`` where ``adjustments`` is a
        human-readable audit trail of rewrites/drops.
    """
    active = active_signal_names(signals)
    out: dict[str, dict[str, Any]] = {
        ns: dict(facts) for ns, facts in namespaces.items() if isinstance(facts, Mapping)
    }
    offer = out.setdefault("offer", {})
    adjustments: list[str] = []

    inquiry = bool(active & _INQUIRY_SIGNALS)
    positive = bool(active & _POSITIVE_COMMIT_SIGNALS)

    # --- interest_signal -------------------------------------------------
    interest = offer.get("offer.interest_signal")
    if interest == "confirmed":
        if active & {"interest_negative"}:
            offer["offer.interest_signal"] = "needs_more_info"
            adjustments.append(
                "downgraded offer.interest_signal confirmed→needs_more_info "
                "(interest_negative)"
            )
        elif inquiry and not positive:
            offer["offer.interest_signal"] = "needs_more_info"
            adjustments.append(
                "downgraded offer.interest_signal confirmed→needs_more_info "
                "(inquiry signals, no interest_positive/accepts_terms)"
            )
        elif not positive:
            offer["offer.interest_signal"] = "needs_more_info"
            adjustments.append(
                "downgraded offer.interest_signal confirmed→needs_more_info "
                "(no interest_positive/accepts_terms)"
            )

    # --- deliverables (committed → proposed on inquiry) ------------------
    if inquiry and "accepts_terms" not in active:
        for committed, proposed in _DELIVERABLE_REWRITES:
            if committed not in offer:
                continue
            val = offer.pop(committed)
            if proposed not in offer:
                offer[proposed] = val
            adjustments.append(f"rewrote {committed}→{proposed} (inquiry signals)")

        if offer.get("offer.usage_rights_discussed"):
            offer.pop("offer.usage_rights_discussed", None)
            adjustments.append(
                "dropped offer.usage_rights_discussed (inquiry without accepts_terms)"
            )

    # --- compensation accept ---------------------------------------------
    if (
        "offer.agreed_terms" in offer
        and "accepts_terms" not in active
        and "continues_without_objection" not in active
    ):
        offer.pop("offer.agreed_terms", None)
        adjustments.append("dropped offer.agreed_terms (no accepts_terms)")

    # --- product locks on product inquiry --------------------------------
    if any(k in offer for k in _PRODUCT_LOCK_KEYS):
        product_inquiry = bool(active & _PRODUCT_INQUIRY_SIGNALS)
        if product_inquiry and not positive:
            dropped = [k for k in _PRODUCT_LOCK_KEYS if k in offer]
            for k in dropped:
                offer.pop(k, None)
            adjustments.append(
                "dropped product lock keys "
                f"{', '.join(dropped)} (product inquiry without confirmation)"
            )

    if not offer:
        out.pop("offer", None)
    else:
        out["offer"] = offer

    return out, adjustments


def should_sanitize_classifier_source(source: str) -> bool:
    """True when ``source`` denotes an inbound email classifier write."""
    return isinstance(source, str) and source.startswith("email:")
