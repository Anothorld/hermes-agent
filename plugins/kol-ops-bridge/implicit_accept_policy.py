"""Deterministic implicit-accept and contract-defer policy for commerce goals.

When ``campaign_config.implicit_accept_enabled`` is on (default), KOL replies
that show continued cooperation without objection can satisfy compensation
facts without an explicit "yes I agree". Gifted/barter paths may defer final
term confirmation to contract signing.

Pure: no DB writes here — callers merge returned facts into ``write_facts_multi``.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Iterable, Mapping, Optional

from . import classifier_facts as cf
from . import deliverables_spec as ds

_BLOCKER_SIGNALS = frozenset({
    "interest_negative",
    "paid_only_stance",
    "proposes_rate",
    "counter_offer",
    "declines_contract",
})

_CONTINUATION_SIGNALS = frozenset({
    "interest_positive",
    "accepts_terms",
    "continues_without_objection",
    "address_provided",
})

_PAYING_MODES = frozenset({"paid", "hybrid", "commission"})

_QUOTE_FACT_KEYS = (
    "offer.kol_paid_quote",
    "offer.kol_quoted_amount",
)

_NON_PAYING_MODES = frozenset({"gifted", "gifted_no_product", "free_product"})

_BRAND_TERMS_KEYWORDS = (
    "deliverable",
    "instagram",
    "tiktok",
    "youtube",
    "cross-post",
    "cross post",
    "reel",
    "video",
    "post",
    "ad code",
    "spark code",
    "usage rights",
    "organic",
    "platform",
)


def normalize_campaign_policy_cfg(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Apply global-default policy flags to a ``campaign_config`` row."""
    out = dict(cfg)
    env_default_on = os.environ.get("KOL_IMPLICIT_ACCEPT_DEFAULT", "1").strip().lower()
    env_global_off = env_default_on in {"0", "false", "no", "off"}
    if env_global_off:
        out["implicit_accept_enabled"] = False
    else:
        out["implicit_accept_enabled"] = bool(
            cfg.get("implicit_accept_enabled", 1),
        )
    out["defer_terms_to_contract"] = bool(
        cfg.get("defer_terms_to_contract", 1),
    )
    out["strict_explicit_accept"] = bool(
        cfg.get("strict_explicit_accept", 0),
    )
    mode = cfg.get("default_compensation_mode")
    out["default_compensation_mode"] = (
        mode if isinstance(mode, str) and mode.strip() else "gifted"
    )
    return out


def policy_active(campaign_cfg: Mapping[str, Any]) -> bool:
    cfg = normalize_campaign_policy_cfg(campaign_cfg)
    return cfg["implicit_accept_enabled"] and not cfg["strict_explicit_accept"]


def _present(state: Mapping[str, Any], key: str) -> bool:
    val = state.get(key)
    if val is None:
        return False
    if isinstance(val, (list, tuple, dict, str)) and len(val) == 0:
        return False
    return True


def has_paid_dispute(
    state: Mapping[str, Any],
    signals: Iterable[Mapping[str, Any]],
    *,
    incoming_offer: Mapping[str, Any] | None = None,
) -> bool:
    """True when implicit gifted accept must not run (paid negotiation path)."""
    incoming = incoming_offer or {}
    active = cf.active_signal_names(signals)
    if active & {"paid_only_stance", "proposes_rate", "counter_offer"}:
        return True
    for key in _QUOTE_FACT_KEYS:
        if _present(state, key) or _present(incoming, key):
            return True
    mode = incoming.get("offer.compensation_mode") or state.get(
        "offer.compensation_mode",
    )
    if mode in _PAYING_MODES:
        return True
    return bool(state.get("approval.over_budget_request"))


def compensation_escalation_open(goal_snapshot: Mapping[str, Any]) -> bool:
    """True when ``compensation_negotiation`` has a blocking escalation."""
    comp = goal_snapshot.get("compensation_negotiation") or {}
    if not isinstance(comp, Mapping):
        return False
    return bool(comp.get("blocking_escalation_id"))


def deliverables_ready(state: Mapping[str, Any]) -> bool:
    return (
        _present(state, "offer.deliverable_platforms")
        and _present(state, "offer.deliverable_count_per_platform")
        and bool(state.get("offer.usage_rights_discussed"))
    )


def _text_has_brand_terms(text: str) -> bool:
    lower = text.lower()
    hits = sum(1 for kw in _BRAND_TERMS_KEYWORDS if kw in lower)
    return hits >= 2 or ("ad code" in lower) or ("cross-post" in lower) or ("cross post" in lower)


def _body_matches_spec(text: str, campaign_cfg: Mapping[str, Any]) -> bool:
    lower = text.lower()
    for phrase in ds.spec_search_phrases(ds.resolve_campaign_deliverables(campaign_cfg)):
        if len(phrase) >= 4 and phrase in lower:
            return True
    return False


def brand_proposed_terms(
    state: Mapping[str, Any],
    thread_meta: Mapping[str, Any],
    *,
    campaign_cfg: Mapping[str, Any] | None = None,
) -> bool:
    proposed = state.get("offer.last_outbound_terms_proposed")
    if isinstance(proposed, str) and proposed.strip():
        if campaign_cfg and _body_matches_spec(proposed, campaign_cfg):
            return True
        return _text_has_brand_terms(proposed)
    if isinstance(proposed, Mapping):
        body = proposed.get("body")
        if isinstance(body, str) and body.strip():
            if campaign_cfg and _body_matches_spec(body, campaign_cfg):
                return True
            return _text_has_brand_terms(body)
    bodies = thread_meta.get("outbound_bodies") or []
    for body in bodies:
        if not isinstance(body, str):
            continue
        if campaign_cfg and _body_matches_spec(body, campaign_cfg):
            return True
        if _text_has_brand_terms(body):
            return True
    return False


def _inquiry_only_without_brand_terms(
    active: set[str],
    state: Mapping[str, Any],
    thread_meta: Mapping[str, Any],
    *,
    campaign_cfg: Mapping[str, Any],
) -> bool:
    if not (active & {"asks_budget", "asks_deliverables"}):
        return False
    if active & (_CONTINUATION_SIGNALS | {"accepts_terms"}):
        return False
    return not brand_proposed_terms(state, thread_meta, campaign_cfg=campaign_cfg)


def should_apply_implicit_accept(
    *,
    state: Mapping[str, Any],
    signals: Iterable[Mapping[str, Any]],
    campaign_cfg: Mapping[str, Any],
    thread_meta: Mapping[str, Any],
    incoming_offer: Mapping[str, Any],
    goal_snapshot: Mapping[str, Any] | None = None,
) -> bool:
    if not policy_active(campaign_cfg):
        return False
    if has_paid_dispute(state, signals, incoming_offer=incoming_offer):
        return False
    if goal_snapshot and compensation_escalation_open(goal_snapshot):
        return False
    if state.get("offer.interest_signal") == "declined":
        return False
    if not deliverables_ready(state):
        return False
    if not brand_proposed_terms(state, thread_meta, campaign_cfg=campaign_cfg):
        return False

    active = cf.active_signal_names(signals)
    if active & _BLOCKER_SIGNALS:
        return False
    if _inquiry_only_without_brand_terms(
        active, state, thread_meta, campaign_cfg=campaign_cfg,
    ):
        return False
    if not (active & _CONTINUATION_SIGNALS):
        return False
    if incoming_offer.get("offer.agreed_terms"):
        return False
    return True


def build_implicit_agreed_terms(
    *,
    state: Mapping[str, Any],
    campaign_cfg: Mapping[str, Any],
    source_message_id: str = "",
) -> dict[str, Any]:
    cfg = normalize_campaign_policy_cfg(campaign_cfg)
    mode = state.get("offer.compensation_mode") or cfg["default_compensation_mode"]
    snapshot: dict[str, Any] = {
        "mode": mode,
        "source": "policy:implicit_accept",
        "inferred_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "deferred_to_contract": bool(cfg["defer_terms_to_contract"]),
    }
    if cfg.get("product_unit_price") is not None:
        snapshot["product_value"] = cfg["product_unit_price"]
    platforms = state.get("offer.deliverable_platforms")
    count = state.get("offer.deliverable_count_per_platform")
    if platforms or count:
        snapshot["deliverables_summary"] = {
            "platforms": platforms,
            "count_per_platform": count,
        }
    spec = ds.resolve_campaign_deliverables(campaign_cfg)
    if spec:
        snapshot["deliverables_spec"] = spec
    if source_message_id:
        snapshot["evidence_message_ids"] = [source_message_id]
    return snapshot


def build_contract_signed_agreed_terms(
    *,
    state: Mapping[str, Any],
    campaign_cfg: Mapping[str, Any],
    incoming_offer: Mapping[str, Any],
) -> dict[str, Any]:
    mode = (
        incoming_offer.get("offer.compensation_mode")
        or state.get("offer.compensation_mode")
        or normalize_campaign_policy_cfg(campaign_cfg)["default_compensation_mode"]
    )
    snapshot: dict[str, Any] = {
        "mode": mode,
        "source": "contract_signed_snapshot",
        "inferred_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    fee = incoming_offer.get("offer.agreed_terms") or state.get("offer.agreed_terms")
    if isinstance(fee, Mapping):
        for key in ("fee", "amount"):
            if fee.get(key) is not None:
                snapshot["fee"] = fee[key]
                break
    elif fee is not None:
        snapshot["fee"] = fee
    platforms = state.get("offer.deliverable_platforms")
    count = state.get("offer.deliverable_count_per_platform")
    if platforms or count:
        snapshot["deliverables_summary"] = {
            "platforms": platforms,
            "count_per_platform": count,
        }
    spec = ds.resolve_campaign_deliverables(campaign_cfg)
    if spec:
        snapshot["deliverables_spec"] = spec
    if campaign_cfg.get("product_unit_price") is not None:
        snapshot["product_value"] = campaign_cfg["product_unit_price"]
    return snapshot


def extract_outbound_bodies(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Pull brand outbound bodies from CAL ``outbound_sent`` events."""
    bodies: list[str] = []
    for ev in events:
        if ev.get("event_type") != "outbound_sent":
            continue
        payload = ev.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        for key in ("sent_body", "body"):
            text = payload.get(key)
            if isinstance(text, str) and text.strip():
                bodies.append(text.strip())
        edit = payload.get("edit_learning")
        if isinstance(edit, Mapping):
            sent = edit.get("sent_body")
            if isinstance(sent, str) and sent.strip():
                bodies.append(sent.strip())
    return bodies


def load_thread_meta_from_events(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    return {"outbound_bodies": extract_outbound_bodies(events)}


def merge_policy_facts(
    namespaces: Mapping[str, Mapping[str, Any]],
    *,
    state: Mapping[str, Any],
    signals: Iterable[Mapping[str, Any]],
    campaign_cfg: Mapping[str, Any],
    thread_meta: Mapping[str, Any],
    source: str,
    goal_snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str], Optional[dict[str, Any]]]:
    """Augment classifier/skill namespaces with policy-derived offer facts.

    Returns:
        ``(merged_namespaces, adjustments, audit_event_or_none)``
    """
    out: dict[str, dict[str, Any]] = {
        ns: dict(facts) for ns, facts in namespaces.items() if isinstance(facts, Mapping)
    }
    offer = dict(out.get("offer") or {})
    adjustments: list[str] = []
    audit: Optional[dict[str, Any]] = None
    cfg = normalize_campaign_policy_cfg(campaign_cfg)

    msg_id = source.split(":", 1)[1] if source.startswith("email:") else ""

    if cf.should_sanitize_classifier_source(source):
        if should_apply_implicit_accept(
            state=state,
            signals=signals,
            campaign_cfg=cfg,
            thread_meta=thread_meta,
            incoming_offer=offer,
            goal_snapshot=goal_snapshot,
        ):
            existing_mode = offer.get("offer.compensation_mode") or state.get(
                "offer.compensation_mode",
            )
            if (
                not existing_mode
                and not has_paid_dispute(state, signals, incoming_offer=offer)
            ):
                offer["offer.compensation_mode"] = cfg["default_compensation_mode"]
                adjustments.append(
                    f"set offer.compensation_mode={cfg['default_compensation_mode']} "
                    "(implicit accept default)"
                )
            effective_mode = (
                offer.get("offer.compensation_mode")
                or state.get("offer.compensation_mode")
                or cfg["default_compensation_mode"]
            )
            if effective_mode in _PAYING_MODES:
                adjustments.append(
                    f"skipped offer.agreed_terms (paying mode={effective_mode})"
                )
            elif not offer.get("offer.agreed_terms"):
                offer["offer.agreed_terms"] = build_implicit_agreed_terms(
                    state=state,
                    campaign_cfg=cfg,
                    source_message_id=msg_id,
                )
                adjustments.append("set offer.agreed_terms (policy:implicit_accept)")
                audit = {
                    "event_type": "policy_implicit_accept_applied",
                    "source_message_id": msg_id,
                    "adjustments": list(adjustments),
                }

    signed = offer.get("offer.contract_signed")
    if signed is True or signed == "true":
        if not state.get("offer.agreed_terms") and not offer.get("offer.agreed_terms"):
            offer["offer.agreed_terms"] = build_contract_signed_agreed_terms(
                state=state,
                campaign_cfg=cfg,
                incoming_offer=offer,
            )
            adjustments.append("backfill offer.agreed_terms (contract_signed_snapshot)")

    if offer:
        out["offer"] = offer
    elif "offer" in out:
        del out["offer"]

    return out, adjustments, audit


__all__ = [
    "normalize_campaign_policy_cfg",
    "policy_active",
    "has_paid_dispute",
    "compensation_escalation_open",
    "deliverables_ready",
    "brand_proposed_terms",
    "should_apply_implicit_accept",
    "build_implicit_agreed_terms",
    "build_contract_signed_agreed_terms",
    "extract_outbound_bodies",
    "load_thread_meta_from_events",
    "merge_policy_facts",
]
