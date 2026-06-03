"""Persist optional Nox integration knobs on ``campaign_config``.

Nox fields are stored in ``nox_integration_json`` (CAL column) and merged
into the dict returned by :func:`cal.get_campaign_config` so Console gates
and ``nox_kol_tool.py`` see a flat ``campaign_config`` object.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

NOX_INTEGRATION_KEYS: frozenset[str] = frozenset(
    {
        "nox_quota_enabled",
        "nox_supplement_enabled",
        "nox_cache_enabled",
        "nox_monthly_budget",
        "nox_supplement_max_calls",
        "nox_cache_retain_months",
        "nox_cache_timezone",
        "nox_diligence_dimensions",
    }
)


def pick_nox_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return only Nox integration keys present in ``fields``."""
    return {k: fields[k] for k in NOX_INTEGRATION_KEYS if k in fields}


def parse_nox_integration_json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def merge_nox_integration(
    existing: Mapping[str, Any],
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Shallow-merge ``updates`` onto ``existing`` Nox integration blob."""
    merged = dict(existing)
    for key, value in updates.items():
        if key in NOX_INTEGRATION_KEYS:
            merged[key] = value
    return merged


def flatten_nox_into_config(out: dict[str, Any]) -> dict[str, Any]:
    """Pop ``nox_integration_json`` and expose Nox keys on ``out``."""
    blob = parse_nox_integration_json(out.pop("nox_integration_json", None))
    for key, value in blob.items():
        if key in NOX_INTEGRATION_KEYS:
            out[key] = value
    return out


def encode_nox_integration(blob: Mapping[str, Any]) -> str:
    return json.dumps(dict(blob), ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "NOX_INTEGRATION_KEYS",
    "encode_nox_integration",
    "flatten_nox_into_config",
    "merge_nox_integration",
    "parse_nox_integration_json",
    "pick_nox_fields",
]
