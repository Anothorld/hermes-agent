"""Queue ``kol-creator-brief-refresh`` gateway runs (Console-triggered)."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .audit import write_audit
from .bridge_agent_contract_loader import (
    gateway_contract_for_brief,
    terminal_safety_rules,
)
from .bridge_client import BridgeClient, BridgeError
from .bridge_runtime import ensure_gateway_bridge_key
from .config import get_settings
from .db import _connect
from .gateway_client import GatewayClient, GatewayError
from .gateway_http import http_exception_from_gateway_start
from .launch_accept import launch_or_accept
from .run_launch_queue import new_pending_run_id
from .run_registry import finalize_run_id, get_inflight_run, register_run

_REPO_ROOT = str(Path(__file__).resolve().parents[4])


def creator_brief_refresh_session_id(
    env: str,
    identity_id: int,
    run_token: str,
) -> str:
    """Build gateway ``session_id`` for one creator-brief refresh run."""
    return f"kol-creator-brief-refresh:{env}:{identity_id}:{run_token}"


CREATOR_BRIEF_REFRESH_INSTRUCTIONS = (
    "You are running the `kol-creator-brief-loader` skill for ONE specific\n"
    "KOL identity at the request of the web console operator. Do NOT loop\n"
    "over any other identity in this campaign.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    f"{gateway_contract_for_brief(compact=True)}\n"
    f"{terminal_safety_rules(repo_root=_REPO_ROOT)}\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- CLI failures print JSON on **stdout**. Empty output + exit 2 → read\n"
    "  stdout for `error`/`hint`; never fall back to execute_code.\n"
    "\n"
    "## Pipeline\n"
    "1. Read identity facts via `get-facts --identity-id <id> --env <env>`.\n"
    "   If all 6 creator-brief keys are present AND\n"
    "   `identity.content_pillars_discovered_at` is within 90 days, stop\n"
    "   immediately and report `{skipped: \"brief_already_fresh\"}`.\n"
    "2. Invoke `kol-creator-brief-loader` with `force_refresh: true`, the\n"
    "   supplied identity_id, env, and campaign_id when present. The skill\n"
    "   uses built-in `browser_*` on local debug Chrome (never MCP Chrome).\n"
    "   **NEVER** call `veedcrawl_*`, `delegate_task`, `execute_code`\n"
    "   (browser/hermes_tools imports), or terminal HTTP scraping.\n"
    "3. On success, report `{refreshed: true, brief_status: \"fresh\"}`. On\n"
    "   total failure, report `{refreshed: false, brief_status: \"unavailable\"}`\n"
    "   — do NOT open escalations; outreach will flag `low_personalization`.\n"
    "\n"
    "## Browser discipline\n"
    "- Local debug Chrome auto-starts on the first `browser_*` call.\n"
    "- Respect the skill's 5 page-load budget; never loop on the same URL.\n"
)


def compose_creator_brief_refresh_brief(
    *,
    identity_id: int,
    handle: str | None,
    env: str,
    campaign_id: str | None,
    actor_email: str,
    brief_status: str | None = None,
) -> str:
    """Build the gateway input brief for a single-identity brief refresh run."""
    lines = [
        "# kol_creator_brief_refresh",
        f"identity_id: {identity_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
        "force_refresh: true",
    ]
    if handle:
        lines.append(f"handle: {handle}")
    if campaign_id:
        lines.append(f"campaign_id: {campaign_id}")
    if brief_status:
        lines.append(f"prior_brief_status: {brief_status}")
    lines.extend([
        "",
        "# required_next_step",
        (
            "Refresh the creator brief for the single identity_id above by "
            "invoking `kol-creator-brief-loader` with `force_refresh: true`. "
            "Do NOT loop over any other identity."
        ),
    ])
    return "\n".join(lines)


async def _start_creator_brief_refresh_run(
    gateway: GatewayClient,
    *,
    dedup_key: str,
    brief: str,
    session_id: str,
    on_success: Any = None,
    on_error: Any = None,
    job_meta: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    async def _start() -> dict[str, Any]:
        return await gateway.start_run_with_retry(
            input=brief,
            instructions=CREATOR_BRIEF_REFRESH_INSTRUCTIONS,
            session_id=session_id,
        )

    try:
        return await launch_or_accept(
            gateway,
            _start,
            session_id=session_id,
            dedup_key=dedup_key,
            kind="creator_brief_refresh",
            on_success=on_success,
            on_error=on_error,
            job_meta=job_meta,
        )
    except GatewayError as exc:
        raise http_exception_from_gateway_start(
            exc, action_label="刷新创作者简介",
        ) from exc


async def dispatch_creator_brief_refresh_for_identity(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    identity_id: int,
    env: str,
    campaign_id: str | None,
    actor_email: str,
    actor_user_id: int,
    ident: Mapping[str, Any] | None = None,
    handle: str | None = None,
    brief_status: str | None = None,
    audit_action: str = "kol.creator_brief.refresh",
    source: str = "manual",
) -> dict[str, Any]:
    """Queue ``kol-creator-brief-refresh`` when brief is missing or stale."""
    if ident is None:
        try:
            ident = await bridge.get_identity(identity_id)
        except BridgeError:
            ident = {}
    if not ident:
        return {
            "identity_id": identity_id,
            "status": "skipped",
            "reason": "identity_not_found",
        }

    try:
        status_map = await bridge.batch_creator_brief_status(
            [identity_id], env=env,
        )
    except BridgeError:
        status_map = {}
    readiness = status_map.get(identity_id) or {}
    if readiness.get("ready"):
        return {
            "identity_id": identity_id,
            "status": "skipped",
            "reason": "brief_already_fresh",
            "creator_brief_status": readiness.get("status"),
        }

    dedup_key = f"creator-brief-refresh:{env}:{identity_id}"
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        return {
            "identity_id": identity_id,
            "status": "inflight",
            "run_id": inflight.get("run_id"),
            "started_at": inflight.get("started_at"),
            "handle": handle,
            "creator_brief_status": readiness.get("status") or brief_status,
        }

    ensure_gateway_bridge_key()
    run_token = new_pending_run_id()
    session_id = creator_brief_refresh_session_id(env, identity_id, run_token)
    if campaign_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=run_token,
            kind="creator_brief_refresh",
            session_id=session_id,
            dedup_key=dedup_key,
        )
        conn.commit()

    resolved_handle = handle
    if not resolved_handle:
        ph = ident.get("primary_handle")
        resolved_handle = ph if isinstance(ph, str) else None

    prior_status = str(readiness.get("status") or brief_status or "missing")
    brief = compose_creator_brief_refresh_brief(
        identity_id=identity_id,
        handle=resolved_handle,
        env=env,
        campaign_id=campaign_id,
        actor_email=actor_email,
        brief_status=prior_status,
    )
    campaign_id_val = campaign_id
    identity_id_val = identity_id
    pending_ref = run_token

    async def _on_success(run: dict[str, Any], _result: Any) -> None:
        rid = run.get("run_id") if isinstance(run, dict) else None
        if not (campaign_id_val and pending_ref and isinstance(rid, str) and rid):
            return
        bg = _connect(get_settings().db_path)
        try:
            finalize_run_id(bg, pending_run_id=pending_ref, actual_run_id=rid)
            write_audit(
                bg,
                actor_user_id=actor_user_id,
                action=audit_action,
                target=str(identity_id_val),
                payload={
                    "env": env,
                    "campaign_id": campaign_id_val,
                    "run_id": rid,
                    "async_accept": True,
                    "source": source,
                },
            )
            bg.commit()
        finally:
            bg.close()

    accepted, run = await _start_creator_brief_refresh_run(
        gateway,
        dedup_key=dedup_key,
        brief=brief,
        session_id=session_id,
        on_success=_on_success,
        job_meta={
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "env": env,
            "source": source,
        },
    )

    if accepted:
        return {
            "identity_id": identity_id,
            "status": "accepted",
            "handle": resolved_handle,
            "pending_run_id": run_token,
            "session_id": session_id,
            "job_id": run.get("job_id"),
            "poll": run.get("poll"),
            "creator_brief_status": prior_status,
            "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds",
            ),
        }

    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id and campaign_id:
        finalize_run_id(conn, pending_run_id=run_token, actual_run_id=run_id)
        conn.commit()

    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action=audit_action,
        target=str(identity_id),
        payload={
            "env": env,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "source": source,
        },
    )

    out: dict[str, Any] = {
        "identity_id": identity_id,
        "status": "queued" if isinstance(run, dict) and run.get("_queued") else "started",
        "handle": resolved_handle,
        "run_id": run_id,
        "creator_brief_status": prior_status,
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds",
        ),
    }
    if isinstance(run, dict):
        if run.get("_queued"):
            out["queued"] = True
            out["waited_sec"] = run.get("_waited_sec")
        if run.get("_queue_position"):
            out["queue_position"] = run.get("_queue_position")
    return out


async def dispatch_creator_brief_refresh_for_approved_identities(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    selected_rows: list[dict[str, Any]],
    actor_email: str,
    actor_user_id: int,
    skip_identity_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Queue brief refresh for approved identities with missing/stale brief.

    Identities in ``skip_identity_ids`` are omitted — ``kol-email-discovery``
    Step 6 refreshes the brief in the same browser run when email is queued.
    """
    skip = skip_identity_ids or set()
    identity_ids = [
        int(row["identity_id"])
        for row in selected_rows
        if int(row["identity_id"]) not in skip
    ]
    try:
        status_map = await bridge.batch_creator_brief_status(identity_ids, env=env)
    except BridgeError:
        status_map = {}

    results: list[dict[str, Any]] = []
    for row in selected_rows:
        identity_id = int(row["identity_id"])
        if identity_id in skip:
            continue
        readiness = status_map.get(identity_id) or {}
        if readiness.get("ready"):
            continue
        outcome = await dispatch_creator_brief_refresh_for_identity(
            bridge=bridge,
            gateway=gateway,
            conn=conn,
            identity_id=identity_id,
            env=env,
            campaign_id=campaign_id,
            actor_email=actor_email,
            actor_user_id=actor_user_id,
            handle=row.get("handle"),
            brief_status=str(readiness.get("status") or "missing"),
            audit_action="kol.creator_brief.refresh_on_approve",
            source="approve_shortlist",
        )
        if outcome.get("status") not in ("skipped",):
            results.append(outcome)
    return results
