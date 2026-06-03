"""Deterministic Gate A (Nox diligence-pack) + CAL fact hydration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from .bridge_client import BridgeClient, BridgeError
from .nox_gate import extract_campaign_config, materialize_campaign_config_file
from .nox_quota import fetch_campaign_nox_stats, quota_exhausted_from_stats
from .nox_tool_runner import run_nox_tool

_NOX_BRIDGE = Path(__file__).resolve().parents[4] / "plugins" / "nox-kol-bridge"
if str(_NOX_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_NOX_BRIDGE))

from internal.diligence_facts import (  # noqa: E402
    identity_facts_from_diligence,
    merge_existing_follower_facts,
)

_PLATFORM_URL_KEYS: dict[str, str] = {
    "youtube": "identity.youtube_profile_url",
    "tiktok": "identity.tiktok_profile_url",
    "instagram": "identity.instagram_profile_url",
}


def _facts_map(facts_resp: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(facts_resp, dict):
        return {}
    raw = facts_resp.get("facts")
    return dict(raw) if isinstance(raw, dict) else {}


def resolve_diligence_params(
    ident: Mapping[str, Any],
    facts_resp: Mapping[str, Any] | None,
) -> dict[str, Optional[str]]:
    """CLI args for ``diligence-pack``."""
    facts = _facts_map(facts_resp)
    nox_id = facts.get("identity.nox_creator_id") or ident.get("nox_creator_id")
    platform = str(ident.get("platform") or "instagram").strip().lower()
    url_key = _PLATFORM_URL_KEYS.get(platform)
    url = facts.get(url_key) if url_key else None
    if not url:
        for key in _PLATFORM_URL_KEYS.values():
            val = facts.get(key)
            if isinstance(val, str) and val.strip():
                url = val.strip()
                if not platform or platform not in _PLATFORM_URL_KEYS:
                    for p, k in _PLATFORM_URL_KEYS.items():
                        if k == key:
                            platform = p
                            break
                break
    return {
        "nox_creator_id": str(nox_id).strip() if nox_id else None,
        "platform": platform if platform in _PLATFORM_URL_KEYS else "instagram",
        "url": str(url).strip() if url else None,
    }


def diligence_eligible(params: Mapping[str, Optional[str]]) -> bool:
    if params.get("nox_creator_id"):
        return True
    return bool(params.get("platform") and params.get("url"))


def _run_diligence_cli(
    *,
    env: str,
    cfg_path: str,
    params: Mapping[str, Optional[str]],
    tz: str,
    monthly_budget: int,
    campaign_id: str,
    identity_id: int,
    lang: str = "en",
) -> dict[str, Any]:
    argv = [
        "diligence-pack",
        "--env",
        env,
        "--gate",
        "shortlist_confirm",
        "--campaign-config-file",
        cfg_path,
        "--timezone",
        tz,
        "--monthly-budget",
        str(monthly_budget),
        "--lang",
        lang,
        "--dimensions",
        "profile,audience,content",
        "--audit-campaign-id",
        campaign_id,
        "--audit-identity-id",
        str(identity_id),
    ]
    if params.get("nox_creator_id"):
        argv.extend(["--nox-creator-id", str(params["nox_creator_id"])])
    else:
        argv.extend(
            [
                "--platform",
                str(params["platform"]),
                "--url",
                str(params["url"]),
            ],
        )
    return run_nox_tool(argv, timeout=240)


async def persist_diligence_facts(
    bridge: BridgeClient,
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    actor_email: str,
    diligence_result: Mapping[str, Any],
    existing_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write all mapped identity facts from a diligence-pack envelope."""
    facts = identity_facts_from_diligence(diligence_result)
    if existing_facts:
        facts = merge_existing_follower_facts(facts, existing_facts)
    if not facts:
        return {"facts_written": 0, "fact_keys": []}
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": facts,
                "source": f"web-gate-a:{actor_email}",
                "env": env,
                "campaign_id": None,
            },
        )
    except BridgeError as exc:
        raise exc
    return {
        "facts_written": len(facts),
        "fact_keys": sorted(facts.keys()),
        "verdict": facts.get("identity.nox_diligence_verdict"),
    }


async def attempt_gate_a_diligence(
    bridge: BridgeClient,
    *,
    identity_id: int,
    ident: Mapping[str, Any],
    campaign_id: str,
    env: str,
    actor_email: str,
    facts_resp: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Gate A synchronously and persist all Nox diligence facts."""
    env_u = env.upper()
    if env_u == "TEST":
        cfg_path = ""
        params = resolve_diligence_params(ident, facts_resp)
        if not diligence_eligible(params):
            return {"skipped": True, "reason": "no_nox_creator_or_url"}
        out = _run_diligence_cli(
            env="TEST",
            cfg_path="",
            params=params,
            tz="Asia/Shanghai",
            monthly_budget=1800,
            campaign_id=campaign_id,
            identity_id=identity_id,
        )
    else:
        try:
            camp = await bridge.get_campaign(campaign_id, env=env)
        except BridgeError as exc:
            return {"skipped": True, "reason": f"campaign_load_failed:{exc}"}
        cfg = extract_campaign_config(camp) if isinstance(camp, dict) else {}
        from .nox_gate import _nox_quota_is_enabled

        if not _nox_quota_is_enabled(cfg):
            return {"skipped": True, "reason": "nox_quota_disabled"}
        params = resolve_diligence_params(ident, facts_resp)
        if not diligence_eligible(params):
            return {"skipped": True, "reason": "no_nox_creator_or_url"}
        try:
            stats = await fetch_campaign_nox_stats(bridge, campaign_id, env=env)
        except RuntimeError as exc:
            return {"skipped": True, "reason": f"stats_failed:{exc}"}
        if quota_exhausted_from_stats(stats):
            return {"quota_exhausted": True, "skipped": True, "reason": "quota_exhausted"}
        tz = str(cfg.get("nox_cache_timezone") or "Asia/Shanghai")
        budget = int(cfg.get("nox_monthly_budget") or 1800)
        cfg_path = materialize_campaign_config_file(
            campaign_id,
            cfg,
            allowed_gates=("shortlist_confirm",),
        )
        out = _run_diligence_cli(
            env=env_u,
            cfg_path=cfg_path,
            params=params,
            tz=tz,
            monthly_budget=budget,
            campaign_id=campaign_id,
            identity_id=identity_id,
        )

    if out.get("success") is False:
        code = out.get("error_code")
        if code == "NOX_QUOTA_EXCEEDED":
            return {"quota_exhausted": True, "skipped": True, "reason": "quota_exceeded_cli"}
        return {
            "skipped": True,
            "reason": "diligence_cli_failed",
            "detail": out.get("detail") or code,
        }

    existing = _facts_map(facts_resp)
    persisted = await persist_diligence_facts(
        bridge,
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        actor_email=actor_email,
        diligence_result=out,
        existing_facts=existing,
    )
    return {
        "ok": True,
        "sync": True,
        "cache_hit": bool(out.get("cache_hit")),
        "cache_month": out.get("cache_month"),
        "api_calls": out.get("api_calls"),
        **persisted,
    }
