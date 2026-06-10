"""Deterministic Gate B (Nox contacts) before browser email-discovery."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from .bridge_client import BridgeClient, BridgeError

_NOX_BRIDGE = Path(__file__).resolve().parents[4] / "plugins" / "nox-kol-bridge"
if str(_NOX_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_NOX_BRIDGE))

from internal.diligence_facts import identity_facts_from_contacts  # noqa: E402
from .nox_gate import _nox_quota_is_enabled, extract_campaign_config, materialize_campaign_config_file
from .nox_quota import fetch_campaign_nox_stats, quota_exhausted_from_stats
from .nox_tool_runner import run_nox_tool

_PLATFORM_URL_KEYS: dict[str, str] = {
    "youtube": "identity.youtube_profile_url",
    "tiktok": "identity.tiktok_profile_url",
    "instagram": "identity.instagram_profile_url",
}

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", re.IGNORECASE)


def _facts_map(facts_resp: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(facts_resp, dict):
        return {}
    raw = facts_resp.get("facts")
    return dict(raw) if isinstance(raw, dict) else {}


def resolve_nox_contacts_params(
    ident: Mapping[str, Any],
    facts_resp: Mapping[str, Any] | None,
) -> dict[str, Optional[str]]:
    """Resolve CLI args for ``nox_kol_tool.py contacts``."""
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
                break
    return {
        "nox_creator_id": str(nox_id).strip() if nox_id else None,
        "platform": platform if platform in _PLATFORM_URL_KEYS else "instagram",
        "url": str(url).strip() if url else None,
    }


def gate_b_eligible(params: Mapping[str, Optional[str]]) -> bool:
    """True when Nox contacts can run (creator id or platform+url)."""
    if params.get("nox_creator_id"):
        return True
    return bool(params.get("platform") and params.get("url"))


def _email_from_facts(facts_resp: Mapping[str, Any] | None) -> Optional[str]:
    """Return validated ``identity.email`` from CAL facts when present."""
    raw = _facts_map(facts_resp).get("identity.email")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    return normalized if _EMAIL_RE.match(normalized) else None


def classify_nox_contacts_cli_failure(out: Mapping[str, Any]) -> dict[str, Any]:
    """Map ``nox_kol_tool.py contacts`` failure to Console gate_b metadata."""
    code = out.get("error_code")
    detail = str(out.get("detail") or code or "")
    if code == "NOX_QUOTA_EXCEEDED":
        return {
            "quota_exhausted": True,
            "skipped": True,
            "reason": "nox_saas_quota_exhausted",
            "detail": detail,
        }
    if "40017" in detail or "SaaS 40017" in detail:
        return {
            "skipped": True,
            "reason": "nox_upstream_error",
            "detail": detail,
            "upstream_code": "40017",
        }
    return {
        "skipped": True,
        "reason": "contacts_cli_failed",
        "detail": detail,
    }


async def persist_nox_contact_email(
    bridge: BridgeClient,
    *,
    identity_id: int,
    ident: Mapping[str, Any],
    email: str,
    env: str,
    campaign_id: str,
    actor_email: str,
    contacts_result: Mapping[str, Any],
) -> None:
    """Write ``primary_email`` + Nox provenance facts (Gate B hit)."""
    handle = ident.get("primary_handle")
    if not handle:
        raise ValueError("identity has no primary_handle")
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise ValueError("invalid email from Nox contacts")
    await bridge.upsert_identity(
        {
            "primary_handle": handle,
            "platform": ident.get("platform") or "instagram",
            "primary_email": normalized,
            "env": env,
        },
    )
    facts = identity_facts_from_contacts(
        contacts_result,
        email=normalized,
    )
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": facts,
                "source": f"web-gate-b:{actor_email}",
                "env": env,
                "campaign_id": None,
            },
        )
    except BridgeError:
        pass


def _run_contacts_cli(
    *,
    env: str,
    cfg_path: str,
    params: Mapping[str, Optional[str]],
    tz: str,
    monthly_budget: int,
) -> dict[str, Any]:
    argv = [
        "contacts",
        "--env",
        env,
        "--gate",
        "pre_outreach_confirm",
        "--campaign-config-file",
        cfg_path,
        "--timezone",
        tz,
        "--monthly-budget",
        str(monthly_budget),
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
    return run_nox_tool(argv)


async def attempt_gate_b_contacts(
    bridge: BridgeClient,
    *,
    identity_id: int,
    ident: Mapping[str, Any],
    campaign_id: str,
    env: str,
    actor_email: str,
    facts_resp: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Gate B synchronously when campaign has ``nox_quota_enabled``.

    Returns metadata including ``email_found``, ``skipped``, ``quota_exhausted``.
    """
    if env.upper() != "LIVE":
        return {"skipped": True, "reason": "test_env"}
    try:
        camp = await bridge.get_campaign(campaign_id, env=env)
    except BridgeError as exc:
        return {"skipped": True, "reason": f"campaign_load_failed:{exc}"}
    cfg = extract_campaign_config(camp) if isinstance(camp, dict) else {}
    if not _nox_quota_is_enabled(cfg):
        return {"skipped": True, "reason": "nox_quota_disabled"}

    promoted = _email_from_facts(facts_resp)
    if promoted and not str(ident.get("primary_email") or "").strip():
        handle = ident.get("primary_handle")
        if handle:
            await bridge.upsert_identity(
                {
                    "primary_handle": handle,
                    "platform": ident.get("platform") or "instagram",
                    "primary_email": promoted,
                    "env": env,
                },
            )
            return {
                "email_found": True,
                "email": promoted,
                "gate_b": True,
                "promoted_from_facts": True,
            }

    params = resolve_nox_contacts_params(ident, facts_resp)
    if not gate_b_eligible(params):
        # #region agent log
        import json as _json, time as _time
        with open("/Users/arnold/agent_prj/.cursor/debug-f680ad.log", "a") as _df:
            _df.write(_json.dumps({"sessionId": "f680ad", "hypothesisId": "H2", "location": "nox_contacts_sync.py:attempt_gate_b_contacts", "message": "gate_b not eligible", "data": {"identity_id": identity_id, "params": params}, "timestamp": int(_time.time() * 1000)}) + "\n")
        # #endregion
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
        allowed_gates=("pre_outreach_confirm",),
    )
    out = _run_contacts_cli(
        env=env,
        cfg_path=cfg_path,
        params=params,
        tz=tz,
        monthly_budget=budget,
    )
    # #region agent log
    import json as _json, time as _time
    with open("/Users/arnold/agent_prj/.cursor/debug-f680ad.log", "a") as _df:
        _df.write(_json.dumps({"sessionId": "f680ad", "hypothesisId": "H1-H3", "location": "nox_contacts_sync.py:attempt_gate_b_contacts", "message": "nox contacts cli result", "data": {"identity_id": identity_id, "success": out.get("success"), "error_code": out.get("error_code"), "email": (out.get("normalized_summary") or {}).get("email"), "cache_hit": out.get("cache_hit")}, "timestamp": int(_time.time() * 1000)}) + "\n")
    # #endregion
    if out.get("success") is False:
        return classify_nox_contacts_cli_failure(out)

    summary = out.get("normalized_summary") or {}
    email = summary.get("email")
    if isinstance(email, str) and email.strip():
        await persist_nox_contact_email(
            bridge,
            identity_id=identity_id,
            ident=ident,
            email=email,
            env=env,
            campaign_id=campaign_id,
            actor_email=actor_email,
            contacts_result=out,
        )
        return {
            "email_found": True,
            "email": email.strip().lower(),
            "gate_b": True,
            "cache_hit": bool(out.get("cache_hit")),
        }
    return {"email_found": False, "gate_b": True, "cache_hit": bool(out.get("cache_hit"))}
