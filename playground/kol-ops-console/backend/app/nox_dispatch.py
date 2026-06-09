"""Dispatch Nox-related gateway runs from Console (approve / batch flows)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from .audit import write_audit
from .bridge_client import BridgeClient, BridgeError
from .bridge_runtime import ensure_gateway_bridge_key
from .gateway_client import GatewayClient, GatewayError
from .nox_gate import extract_campaign_config, materialize_campaign_config_file, require_nox_quota_enabled
from .run_registry import get_inflight_run, register_run

_REPO_ROOT = str(Path(__file__).resolve().parents[4])

_OUTREACH_SESSION_PREFIX = "kol-campaign-outreach"

_NOX_CONTACTS_BATCH_INSTRUCTIONS = (
    "You are running Gate B Nox contacts for multiple KOLs after shortlist approval.\n"
    f"- Nox: python3 {_REPO_ROOT}/plugins/nox-kol-bridge/scripts/nox_kol_tool.py\n"
    f"- Bridge: {_REPO_ROOT}/plugins/kol-ops-bridge/scripts/kol-bridge-cli\n"
    "Process identity_ids in order. Skip if primary_email already set.\n"
    "Use contacts --gate pre_outreach_confirm with --campaign-config-file.\n"
    "DO NOT use browser_* or mcp_chrome_devtools_* tools in this run.\n"
)


async def identity_ids_missing_email(
    bridge: BridgeClient,
    identity_ids: list[int],
) -> list[int]:
    """Return identity ids that have no primary_email on file."""
    missing: list[int] = []
    for iid in identity_ids:
        try:
            ident = await bridge.get_identity(iid)
        except BridgeError:
            continue
        if not ident:
            continue
        if not str(ident.get("primary_email") or "").strip():
            missing.append(iid)
    return missing


async def dispatch_nox_contacts_batch(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    identity_ids: list[int],
    actor_user_id: int,
    actor_email: str,
) -> Optional[dict[str, Any]]:
    """Start a batch Nox contacts gateway run when quota is enabled."""
    if env.upper() == "TEST":
        cfg: dict[str, Any] = {}
    else:
        try:
            cfg = await require_nox_quota_enabled(bridge, campaign_id, env=env)
        except Exception:
            return None
    if not cfg.get("nox_quota_enabled"):
        return None
    need = await identity_ids_missing_email(bridge, identity_ids)
    if not need:
        return {"ok": True, "skipped": True, "reason": "all_have_email"}
    dedup_key = f"nox-contacts-batch:{env}:{campaign_id}"
    if get_inflight_run(conn, dedup_key=dedup_key) is not None:
        return {"ok": False, "skipped": True, "reason": "inflight"}
    cfg_path = (
        materialize_campaign_config_file(
            campaign_id,
            cfg,
            allowed_gates=("pre_outreach_confirm",),
        )
        if env.upper() == "LIVE"
        else ""
    )
    ensure_gateway_bridge_key()
    brief = "\n".join([
        "# kol_nox_contacts_batch",
        f"campaign_id: {campaign_id}",
        f"mode: {env}",
        f"campaign_config_file: {cfg_path}",
        f"identity_ids: {','.join(str(i) for i in need)}",
        f"requested_by: {actor_email}",
    ])
    session_id = f"kol-nox-contacts-batch:{env}:{campaign_id}"
    try:

        async def _start_batch() -> dict[str, Any]:
            return await gateway.start_run(
                input=brief,
                instructions=_NOX_CONTACTS_BATCH_INSTRUCTIONS,
                session_id=session_id,
            )

        run = await gateway.launch_via_queue(
            _start_batch,
            session_id=session_id,
            dedup_key=dedup_key,
        )
    except GatewayError:
        return {"ok": False, "error": "gateway_start_failed"}
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id:
        gateway.ensure_run_drained(run_id)
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=run_id,
            kind="draft",
            session_id=f"kol-nox-contacts-batch:{env}:{campaign_id}",
            dedup_key=dedup_key,
        )
    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action="kol.nox.contacts_batch",
        target=campaign_id,
        payload={"env": env, "run_id": run_id, "identity_ids": need},
    )
    return {"ok": True, "run_id": run_id, "identity_ids": need}


async def campaign_nox_config(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    env: str = "LIVE",
) -> dict[str, Any]:
    try:
        camp = await bridge.get_campaign(campaign_id, env=env)
    except BridgeError:
        return {}
    return extract_campaign_config(camp) if isinstance(camp, dict) else {}
