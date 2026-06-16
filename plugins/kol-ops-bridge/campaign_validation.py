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
    _check_deliverables_spec(v, candidate)
    _check_audit(v, candidate)
    _check_nox_config(v, candidate)

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


def normalize_deliverable_count_per_platform(value: Any) -> Optional[int]:
    """Coerce ``campaign_config.deliverable_count_per_platform`` to a scalar int.

    Storage (CAL ``campaign_config`` row + ``upsert-campaign``) is a single
  INTEGER applied uniformly to every entry in ``deliverable_platforms``. Do
    not confuse with ``offer.deliverable_count_per_platform`` conversation
    facts, which may be per-platform maps during negotiation.

    Args:
        value: Raw candidate from intake JSON.

    Returns:
        Positive int, or ``None`` when ``value`` is blank.

    Raises:
        ValueError: Non-uniform per-platform map, invalid type, or non-positive.
    """
    if _is_blank(value):
        return None
    if isinstance(value, bool):
        raise ValueError("deliverable_count_per_platform must be a positive int, not a boolean")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("deliverable_count_per_platform must be a positive int")
        return value
    if isinstance(value, dict):
        if not value:
            raise ValueError("deliverable_count_per_platform map must not be empty")
        nums: list[int] = []
        for platform, n in value.items():
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                raise ValueError(
                    f"deliverable_count_per_platform[{platform!r}]: "
                    "count must be a positive int"
                )
            nums.append(n)
        unique = set(nums)
        if len(unique) != 1:
            raise ValueError(
                "deliverable_count_per_platform must be a single integer for "
                "campaign_config (same count on every platform in "
                "deliverable_platforms), not a per-platform map with differing "
                f"values; got {value!r}. Use the minimum shared count or open an "
                "escalation if platforms truly need different counts."
            )
        return nums[0]
    raise ValueError(
        "deliverable_count_per_platform must be a positive int "
        "(e.g. 1), not a per-platform dict — that shape is only for "
        "offer.* negotiation facts, not campaign_config upserts"
    )


def _check_counts(v: _Verdict, c: Mapping[str, Any]) -> None:
    counts = c.get("deliverable_count_per_platform")
    if _is_blank(counts):
        return
    try:
        normalized = normalize_deliverable_count_per_platform(counts)
    except ValueError as exc:
        v.fail("deliverable_count_per_platform", str(exc))
        return
    if normalized is not None:
        v.normalized["deliverable_count_per_platform"] = normalized


def _check_deliverables_spec(v: _Verdict, c: Mapping[str, Any]) -> None:
    raw = c.get("campaign_deliverables_json")
    if raw is None or _is_blank(raw):
        return
    from . import deliverables_spec as ds

    verdict = ds.validate_spec(raw)
    if not verdict["valid"]:
        v.fail("campaign_deliverables_json", "; ".join(verdict["errors"][:3]))
        return
    v.normalized["campaign_deliverables_json"] = verdict["normalized"]


_NOX_BOOL_KEYS = frozenset({"nox_quota_enabled", "nox_supplement_enabled", "nox_cache_enabled"})
_NOX_INT_KEYS = frozenset(
    {"nox_monthly_budget", "nox_supplement_max_calls", "nox_cache_retain_months"}
)
_NOX_STR_KEYS = frozenset({"nox_cache_timezone"})


def _check_nox_config(v: _Verdict, c: Mapping[str, Any]) -> None:
    """Optional Nox integration knobs on ``campaign_config``."""
    for key in _NOX_BOOL_KEYS:
        val = c.get(key)
        if val is None:
            continue
        if not isinstance(val, bool):
            v.fail(key, "must be boolean")
        else:
            v.normalized[key] = val
    for key in _NOX_INT_KEYS:
        val = c.get(key)
        if val is None:
            continue
        try:
            ival = int(val)
        except (TypeError, ValueError):
            v.fail(key, "must be integer")
            continue
        if ival < 0:
            v.fail(key, "must be >= 0")
            continue
        if key == "nox_monthly_budget" and ival > 2000:
            v.fail(key, "must be <= 2000 (plan quota ceiling)")
            continue
        v.normalized[key] = ival
    for key in _NOX_STR_KEYS:
        val = c.get(key)
        if val is None:
            continue
        if not isinstance(val, str) or not val.strip():
            v.fail(key, "must be a non-empty string")
        else:
            v.normalized[key] = val.strip()
    dims = c.get("nox_diligence_dimensions")
    if dims is not None:
        if not isinstance(dims, list) or not dims:
            v.fail("nox_diligence_dimensions", "must be a non-empty list")
        else:
            allowed = {"profile", "audience", "content", "cooperation"}
            bad = [d for d in dims if d not in allowed]
            if bad:
                v.fail("nox_diligence_dimensions", f"unknown dimensions: {bad}")
            else:
                v.normalized["nox_diligence_dimensions"] = dims


def _check_audit(v: _Verdict, c: Mapping[str, Any]) -> None:
    md = c.get("audit_standards_md")
    if not isinstance(md, str) or not md.strip():
        return
    if len(md.strip()) < 50:
        v.fail("audit_standards_md", "must be >= 50 characters (no placeholder)")
        return
    v.normalized["audit_standards_md"] = md.strip()


__all__ = [
    "validate_campaign_config",
    "normalize_deliverable_count_per_platform",
    "DEFAULT_SKU_REGEX",
    "REQUIRED_FIELDS",
]
