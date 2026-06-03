"""Enforce ``campaign_config`` Nox knobs before LIVE API calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from schemas import DEFAULT_MONTHLY_BUDGET

from internal.console_dispatch import verify_console_dispatch
from internal.exceptions import NoxCampaignGateError  # noqa: F401 — re-exported


def load_campaign_config_file(path: Optional[str]) -> dict[str, Any]:
    """Load campaign_config JSON from a file path."""
    if not path or not str(path).strip():
        return {}
    p = Path(path).expanduser()
    if not p.is_file():
        raise NoxCampaignGateError(f"campaign config file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NoxCampaignGateError(f"invalid campaign config JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise NoxCampaignGateError("campaign config must be a JSON object")
    return raw


def resolve_monthly_budget(
    campaign_config: Mapping[str, Any],
    cli_budget: int,
) -> int:
    """Prefer ``nox_monthly_budget`` from campaign when set."""
    cfg_budget = campaign_config.get("nox_monthly_budget")
    if cfg_budget is None:
        return cli_budget
    try:
        return int(cfg_budget)
    except (TypeError, ValueError) as exc:
        raise NoxCampaignGateError("nox_monthly_budget must be integer") from exc


def assert_live_allowed(
    env: str,
    campaign_config: Mapping[str, Any],
    *,
    operation: str,
    gate: str = "",
) -> None:
    """Block LIVE calls when campaign Nox integration is disabled.

    Args:
        env: ``TEST`` or ``LIVE``.
        campaign_config: Parsed ``campaign_config`` object.
        operation: One of ``diligence_pack``, ``contacts``, ``creator_search``,
            ``monitor_setup``.
        gate: Audit gate label; must match operation and Console dispatch claim.
    """
    if env.upper() == "TEST":
        return
    if not campaign_config:
        raise NoxCampaignGateError(
            "LIVE requires --campaign-config-file with campaign_config JSON "
            "(set nox_quota_enabled: true)"
        )
    if not campaign_config.get("nox_quota_enabled"):
        raise NoxCampaignGateError(
            "campaign_config.nox_quota_enabled must be true for LIVE Nox calls"
        )
    if operation == "creator_search" and not campaign_config.get(
        "nox_supplement_enabled"
    ):
        raise NoxCampaignGateError(
            "campaign_config.nox_supplement_enabled must be true for supplement search"
        )
    if campaign_config.get("nox_cache_enabled") is False:
        raise NoxCampaignGateError(
            "campaign_config.nox_cache_enabled is false; enable cache or use TEST"
        )
    if gate:
        verify_console_dispatch(campaign_config, gate=gate, operation=operation)


def resolve_campaign_id(campaign_config: Mapping[str, Any]) -> Optional[str]:
    raw = campaign_config.get("campaign_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def resolve_supplement_max_calls(campaign_config: Mapping[str, Any]) -> int:
    """Default cap per plan when supplement is enabled."""
    raw = campaign_config.get("nox_supplement_max_calls")
    if raw is None:
        return 30
    try:
        return max(0, int(raw))
    except (TypeError, ValueError) as exc:
        raise NoxCampaignGateError("nox_supplement_max_calls must be integer") from exc


def resolve_cache_timezone(
    campaign_config: Mapping[str, Any],
    cli_tz: str,
) -> str:
    """Prefer ``nox_cache_timezone`` from campaign config when set."""
    raw = campaign_config.get("nox_cache_timezone")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return cli_tz


def diligence_dimensions(
    campaign_config: Mapping[str, Any],
    cli_dims: list[str],
) -> list[str]:
    """Use campaign ``nox_diligence_dimensions`` when provided."""
    cfg_dims = campaign_config.get("nox_diligence_dimensions")
    if isinstance(cfg_dims, list) and cfg_dims:
        return [str(d) for d in cfg_dims]
    return cli_dims


__all__ = [
    "DEFAULT_MONTHLY_BUDGET",
    "NoxCampaignGateError",  # re-export for callers
    "assert_live_allowed",
    "diligence_dimensions",
    "load_campaign_config_file",
    "resolve_cache_timezone",
    "resolve_campaign_id",
    "resolve_monthly_budget",
    "resolve_supplement_max_calls",
]
