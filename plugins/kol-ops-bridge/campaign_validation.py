"""Deterministic ``campaign_config`` validation (from `kol-campaign-intake`).

The ``kol-campaign-intake`` skill must NOT paper over missing safety-critical
fields with placeholder defaults (an empty ``sku_whitelist`` silently blocks
every product; ``product_display_name == <sku>`` defeats the cold-outreach
SKU-leak guard). This module encodes those validators as deterministic code so
both the skill and the Web console enforce the *same* rules — the LLM only
extracts candidate values from free text; the gate lives here.

Pure: no DB, no HTTP. ``validate_campaign_config`` returns a structured
verdict; callers map it onto the SKILL's error shapes / a wizard UI.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

# Brand SKU shape, e.g. ``SEB8008``, ``TS-8319``, ``POV-RUG-04``.
DEFAULT_SKU_REGEX = re.compile(r"^[A-Z]{2,5}[\- ]?\d{3,5}[A-Z0-9]*$")

REQUIRED_FIELDS = (
    "product_display_name",
    "sku_whitelist",
    "color_variant_policy",
    "compensation",
    "deliverable_platforms",
    "deliverable_count_per_platform",
    "brief_template_id",
    "audit_standards_md",
)

_COLOR_POLICIES = frozenset({"strict_whitelist", "locked_per_kol", "any_in_whitelist"})
_COMP_MODES = frozenset({"gifted", "paid", "commission", "hybrid"})
_PLATFORMS = frozenset({"instagram", "tiktok", "youtube", "twitter", "blog"})

DEFAULT_ABSURDITY_CEILING_USD = 1_000_000


class _Verdict:
    """Accumulates missing / invalid findings during validation."""

    def __init__(self) -> None:
        self.missing: list[str] = []
        self.invalid: list[dict[str, str]] = []
        self.normalized: dict[str, Any] = {}

    def fail(self, field: str, reason: str) -> None:
        self.invalid.append({"field": field, "reason": reason})


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple)) and len(value) == 0:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def validate_campaign_config(
    candidate: Mapping[str, Any],
    *,
    campaign_id: str,
    sku_regex: Optional[re.Pattern[str]] = None,
    absurdity_ceiling_usd: float = DEFAULT_ABSURDITY_CEILING_USD,
    confirmed_high_budget: bool = False,
) -> dict[str, Any]:
    """Validate an extracted ``campaign_config`` candidate.

    Args:
        candidate: the structured field map an upstream LLM/operator extracted.
        campaign_id: the campaign id (used to reject ``product_display_name``
            that merely echoes the id / a SKU code).
        sku_regex: brand-specific SKU pattern; defaults to ``DEFAULT_SKU_REGEX``.
        absurdity_ceiling_usd: paid amount above which a human must confirm.
        confirmed_high_budget: operator already approved an over-ceiling budget.

    Returns:
        ``{"status": ..., "missing": [...], "invalid": [...],
        "normalized": {...}}`` where ``status`` is one of ``ok`` /
        ``incomplete`` / ``invalid`` / ``cap_review``. The first non-ok status
        in (incomplete, invalid, cap_review) wins, matching the SKILL's
        short-circuit order.
    """
    rx = sku_regex or DEFAULT_SKU_REGEX
    v = _Verdict()

    for field in REQUIRED_FIELDS:
        if _is_blank(candidate.get(field)):
            v.missing.append(field)

    wl = candidate.get("sku_whitelist")
    whitelist = [s for s in wl if isinstance(s, str) and s.strip()] if isinstance(wl, list) else []

    _check_display_name(v, candidate, campaign_id, whitelist, rx)
    _check_whitelist(v, wl, whitelist, rx)
    _check_color_policy(v, candidate)
    cap_amount = _check_compensation(v, candidate)
    _check_platforms(v, candidate)
    _check_counts(v, candidate)
    _check_audit(v, candidate)

    if v.missing:
        return {"status": "incomplete", "missing": sorted(set(v.missing)),
                "invalid": v.invalid, "normalized": v.normalized}
    if v.invalid:
        return {"status": "invalid", "missing": [], "invalid": v.invalid,
                "normalized": v.normalized}
    if cap_amount is not None and cap_amount > absurdity_ceiling_usd and not confirmed_high_budget:
        return {"status": "cap_review", "missing": [], "invalid": [],
                "amount": cap_amount, "normalized": v.normalized}
    return {"status": "ok", "missing": [], "invalid": [], "normalized": v.normalized}


def _check_display_name(v: _Verdict, c: Mapping[str, Any], campaign_id: str,
                        whitelist: list[str], rx: re.Pattern[str]) -> None:
    name = c.get("product_display_name")
    if not isinstance(name, str) or not name.strip():
        return  # handled by missing-check
    name = name.strip()
    if not (2 <= len(name) <= 80):
        v.fail("product_display_name", "must be 2-80 characters")
        return
    if rx.match(name):
        v.fail("product_display_name", "looks like a SKU code; use a human-friendly name")
        return
    if name.lower() == campaign_id.lower():
        v.fail("product_display_name", "must not equal the campaign_id")
        return
    if any(name.lower() == s.lower() for s in whitelist):
        v.fail("product_display_name", "must not equal a sku_whitelist entry")
        return
    v.normalized["product_display_name"] = name


def _check_whitelist(v: _Verdict, raw: Any, whitelist: list[str],
                     rx: re.Pattern[str]) -> None:
    if raw is None:
        return  # missing-check covers absence
    if not isinstance(raw, list) or not whitelist:
        v.fail("sku_whitelist", "must be a non-empty list of SKU codes")
        return
    bad = [s for s in whitelist if not rx.match(s)]
    if bad:
        v.fail("sku_whitelist", f"entries do not match SKU pattern: {bad}")
        return
    v.normalized["sku_whitelist"] = whitelist


def _check_color_policy(v: _Verdict, c: Mapping[str, Any]) -> None:
    policy = c.get("color_variant_policy")
    if policy is None or _is_blank(policy):
        return
    if policy not in _COLOR_POLICIES:
        v.fail("color_variant_policy", f"must be one of {sorted(_COLOR_POLICIES)}")
        return
    v.normalized["color_variant_policy"] = policy


def _check_compensation(v: _Verdict, c: Mapping[str, Any]) -> Optional[float]:
    comp = c.get("compensation")
    if not isinstance(comp, dict) or not comp:
        return None  # missing-check covers absence
    mode = comp.get("default_mode")
    if mode not in _COMP_MODES:
        v.fail("compensation", f"default_mode must be one of {sorted(_COMP_MODES)}")
        return None
    paid = comp.get("paid_max_amount")
    if paid is not None:
        try:
            paid = float(paid)
        except (TypeError, ValueError):
            v.fail("compensation", "paid_max_amount must be numeric")
            return None
        if paid <= 0:
            v.fail("compensation", "paid_max_amount must be > 0")
            return None
    v.normalized["compensation"] = comp
    return paid


def _check_platforms(v: _Verdict, c: Mapping[str, Any]) -> None:
    platforms = c.get("deliverable_platforms")
    if _is_blank(platforms):
        return
    if not isinstance(platforms, list):
        v.fail("deliverable_platforms", "must be a non-empty list")
        return
    bad = [p for p in platforms if p not in _PLATFORMS]
    if bad:
        v.fail("deliverable_platforms", f"unknown platforms: {bad}")
        return
    v.normalized["deliverable_platforms"] = platforms


def _check_counts(v: _Verdict, c: Mapping[str, Any]) -> None:
    counts = c.get("deliverable_count_per_platform")
    if _is_blank(counts):
        return
    if isinstance(counts, int):
        if counts <= 0:
            v.fail("deliverable_count_per_platform", "must be a positive int")
            return
        v.normalized["deliverable_count_per_platform"] = counts
        return
    if isinstance(counts, dict):
        for platform, n in counts.items():
            if not isinstance(n, int) or n <= 0:
                v.fail("deliverable_count_per_platform",
                       f"{platform}: count must be a positive int")
                return
        v.normalized["deliverable_count_per_platform"] = counts
        return
    v.fail("deliverable_count_per_platform", "must be a positive int or platform->int map")


def _check_audit(v: _Verdict, c: Mapping[str, Any]) -> None:
    md = c.get("audit_standards_md")
    if not isinstance(md, str) or not md.strip():
        return
    if len(md.strip()) < 50:
        v.fail("audit_standards_md", "must be >= 50 characters (no placeholder)")
        return
    v.normalized["audit_standards_md"] = md.strip()


__all__ = ["validate_campaign_config", "DEFAULT_SKU_REGEX", "REQUIRED_FIELDS"]
