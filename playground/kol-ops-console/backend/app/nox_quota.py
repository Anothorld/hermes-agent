"""Nox monthly quota helpers for Console (stats, exhaustion, escalations)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import HTTPException, status

from .bridge_client import BridgeClient, BridgeError
from .nox_gate import extract_campaign_config
from .nox_tool_runner import run_nox_tool


def quota_exhausted_from_stats(stats: Mapping[str, Any]) -> bool:
    """True when local ledger ``remaining_estimate`` is zero."""
    if stats.get("quota_exhausted") is True:
        return True
    usage = stats.get("usage") if isinstance(stats, dict) else None
    if not isinstance(usage, Mapping):
        return False
    rem = usage.get("remaining_estimate")
    return isinstance(rem, int) and rem <= 0


async def fetch_campaign_nox_stats(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    env: str,
) -> dict[str, Any]:
    """Run ``cache-stats`` for a campaign (timezone from CAL config)."""
    supplement_max = 30
    cfg: dict[str, Any] = {}
    tz = "Asia/Shanghai"
    try:
        camp = await bridge.get_campaign(campaign_id, env=env)
        cfg = extract_campaign_config(camp) if isinstance(camp, dict) else {}
        if cfg.get("nox_supplement_max_calls") is not None:
            supplement_max = int(cfg["nox_supplement_max_calls"])
        raw_tz = cfg.get("nox_cache_timezone")
        if isinstance(raw_tz, str) and raw_tz.strip():
            tz = raw_tz.strip()
    except BridgeError:
        pass
    argv = [
        "cache-stats",
        "--campaign-id",
        campaign_id,
        "--timezone",
        tz,
    ]
    stats = run_nox_tool(argv)
    if stats.get("success") is False:
        detail = stats.get("detail") or stats.get("error_code") or "nox cache-stats failed"
        raise RuntimeError(str(detail))
    stats["quota_exhausted"] = quota_exhausted_from_stats(stats)
    stats["supplement_max_calls"] = supplement_max
    return stats


def raise_quota_exhausted(*, campaign_id: str, env: str) -> None:
    """409 for Console + FE to show quota banner / escalation CTA."""
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "nox_quota_exhausted",
            "message": (
                "Local Nox monthly budget is exhausted (remaining_estimate=0). "
                "Open an escalation for operator approval before more LIVE Nox API calls."
            ),
            "campaign_id": campaign_id,
            "env": env,
        },
    )


async def assert_nox_quota_available(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    env: str,
) -> dict[str, Any]:
    """Load stats; raise 409 when quota is exhausted (LIVE only)."""
    if env.upper() == "TEST":
        return {}
    stats = await fetch_campaign_nox_stats(bridge, campaign_id, env=env)
    if stats.get("quota_exhausted"):
        raise_quota_exhausted(campaign_id=campaign_id, env=env)
    return stats


async def open_nox_quota_escalation(
    bridge: BridgeClient,
    *,
    campaign_id: str,
    env: str,
    identity_id: Optional[int] = None,
    actor_email: str,
) -> dict[str, Any]:
    """Open ``nox_quota_exhausted`` escalation via bridge."""
    payload: dict[str, Any] = {
        "campaign_id": campaign_id,
        "env": env,
        "goal": "nox_quota_exhausted",
        "reason": (
            "Nox local monthly API budget is exhausted (remaining_estimate=0). "
            "Operator must approve waiting for next cache month, raising budget, "
            "or pausing Nox-gated actions."
        ),
        "question_to_operator": (
            "Nox quota exhausted for this campaign. How should we proceed "
            "(pause Nox calls, adjust nox_monthly_budget, or wait for next month)?"
        ),
        "severity": "high",
        "resume_context": {
            "opened_by": f"web:{actor_email}",
            "source": "kol-ops-console",
            "rule_id": "nox_quota_exhausted",
        },
    }
    if identity_id is not None:
        payload["identity_id"] = identity_id
    return await bridge.open_escalation(payload)
