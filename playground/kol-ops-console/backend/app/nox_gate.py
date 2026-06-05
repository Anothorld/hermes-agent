"""Console-side guards for Nox gateway runs (before Hermes start_run)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from fastapi import HTTPException, status

from .bridge_client import BridgeClient, BridgeError
from .nox_console_dispatch import materialize_with_dispatch


def extract_campaign_config(campaign: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``campaign_config`` from a bridge or gateway payload.

    ``GET /campaigns/{id}`` returns a flat CAL row (``campaign_id``, deliverables,
    Nox knobs, …). Older briefs may nest config under ``campaign_config`` or
    ``facts.campaign_config``.
    """
    nested = campaign.get("campaign_config")
    if isinstance(nested, dict):
        return dict(nested)
    facts = campaign.get("facts")
    if isinstance(facts, dict):
        from_facts = facts.get("campaign_config")
        if isinstance(from_facts, dict):
            return dict(from_facts)
    if campaign.get("campaign_id") is not None:
        return dict(campaign)
    return {}


def _nox_quota_is_enabled(cfg: Mapping[str, Any]) -> bool:
    """Truth test for ``nox_quota_enabled`` (bool or legacy string/int)."""
    raw = cfg.get("nox_quota_enabled")
    if raw is True:
        return True
    if isinstance(raw, str) and raw.strip().lower() in {"true", "1", "yes"}:
        return True
    if raw in (1,):
        return True
    return False


async def require_nox_quota_enabled(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    env: str,
) -> dict[str, Any]:
    """Load campaign and ensure ``nox_quota_enabled`` for LIVE Nox runs."""
    if env.upper() == "TEST":
        return {}
    try:
        campaign = await bridge.get_campaign(campaign_id, env=env)
    except BridgeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"cannot load campaign for Nox gate: {exc}",
        ) from exc
    cfg = extract_campaign_config(campaign)
    if not _nox_quota_is_enabled(cfg):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "nox_quota_disabled",
                "detail": "Set campaign_config.nox_quota_enabled=true before Nox API runs",
            },
        )
    return cfg


async def require_nox_supplement_enabled(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    env: str,
) -> dict[str, Any]:
    """Require supplement flag in addition to quota."""
    cfg = await require_nox_quota_enabled(bridge, campaign_id, env=env)
    if env.upper() == "TEST":
        return cfg
    if not cfg.get("nox_supplement_enabled"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "nox_supplement_disabled",
                "detail": "Set campaign_config.nox_supplement_enabled=true for supplement search",
            },
        )
    return cfg


async def materialize_discovery_nox_config(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    env: str,
) -> str:
    """Write signed config for discovery-time Nox audience screen (LIVE only)."""
    if env.upper() == "TEST":
        return ""
    try:
        campaign = await bridge.get_campaign(campaign_id, env=env)
    except BridgeError:
        return ""
    cfg = extract_campaign_config(campaign)
    if not _nox_quota_is_enabled(cfg):
        return ""
    return materialize_campaign_config_file(
        campaign_id,
        cfg,
        allowed_gates=("discovery_qualify",),
    )


def materialize_campaign_config_file(
    campaign_id: str,
    cfg: Mapping[str, Any],
    *,
    allowed_gates: tuple[str, ...] = (),
) -> str:
    """Write campaign_config for ``nox_kol_tool.py --campaign-config-file``.

    LIVE gated runs must pass ``allowed_gates`` so the JSON includes a signed
    ``nox_console_dispatch`` claim (P3: Console-only trigger enforcement).
    """
    root = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    dest = root / "kol-ops" / "nox_campaign_configs" / f"{campaign_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if allowed_gates:
        payload = materialize_with_dispatch(
            campaign_id,
            cfg,
            allowed_gates=allowed_gates,
        )
    else:
        payload = dict(cfg)
        payload["campaign_id"] = campaign_id
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return str(dest)
