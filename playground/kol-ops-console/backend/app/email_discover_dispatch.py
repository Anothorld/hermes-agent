"""Queue ``kol-email-discovery`` gateway runs (Console-triggered)."""

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
from .db import _connect
from .config import get_settings
from .gateway_client import GatewayClient, GatewayError
from .gateway_http import http_exception_from_gateway_start
from .launch_accept import launch_or_accept
from .launch_rollback import rollback_discover_email_failure
from .nox_contacts_sync import attempt_gate_b_contacts
from .nox_gate import materialize_campaign_config_file, require_nox_quota_enabled
from .nox_quota import raise_quota_exhausted
from .run_launch_queue import new_pending_run_id
from .run_registry import finalize_run_id, get_inflight_run, register_run

_REPO_ROOT = str(Path(__file__).resolve().parents[4])


def email_discover_session_id(
    env: str,
    identity_id: int,
    run_token: str,
) -> str:
    """Build gateway ``session_id`` for one email-discover run.

    ``run_token`` is allocated before ``POST /v1/runs`` (via
    :func:`new_pending_run_id`) and becomes the browser tab-pool key via
    gateway ``task_id = session_id`` — one tab per run, not per identity.
    """
    return f"kol-email-discover:{env}:{identity_id}:{run_token}"


DISCOVER_EMAIL_INSTRUCTIONS = (
    "You are running the `kol-email-discovery` skill for ONE specific\n"
    "KOL identity at the request of the web console operator. Do NOT\n"
    "loop over any other identity in this campaign.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    f"{gateway_contract_for_brief(compact=True)}\n"
    f"{terminal_safety_rules(repo_root=_REPO_ROOT)}\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- CLI failures print JSON on **stdout**. Empty output + exit 2 → read\n"
    "  stdout for `error`/`hint`; never fall back to execute_code.\n"
    "\n"
    "## Pipeline\n"
    "1. Read the identity row. If `primary_email` is already non-empty,\n"
    "   stop immediately and report `{skipped: \"already_has_email\"}`.\n"
    "   Do NOT overwrite an existing email under any circumstance.\n"
    "1b. Gate B (Nox contacts) may already have run on the Console server\n"
    "    before this gateway run (`gate_b_attempted: true` in brief). If not,\n"
    "    and `campaign_config.nox_quota_enabled` with creator id or channel URL,\n"
    "    run `nox_kol_tool.py contacts --gate pre_outreach_confirm` first.\n"
    "    On email hit, stop — do not run browser discovery.\n"
    "2. Invoke the `kol-email-discovery` skill with the supplied\n"
    "   identity_id, env, and campaign_id. The skill resolves the\n"
    "   email from public web sources (link-in-bio, personal site,\n"
    "   media kit) and writes `primary_email` via `upsert-identity`\n"
    "   plus provenance facts via `write-facts-multi`.\n"
    "   **Browser tools:** Tier 1 + Tier 2 use built-in `browser_*` only.\n"
    "   **NEVER** call `mcp_chrome_devtools_*` — remote CDP is unreachable.\n"
    "   **NEVER** use `veedcrawl_*` (video/profile supplement — not for email),\n"
    "   `delegate_task`, `execute_code` (browser/hermes_tools imports), or\n"
    "   `terminal` curl/urllib/requests HTTP fetching (DuckDuckGo, Google,\n"
    "   beacons.ai, bio.link, Instagram, any web page).\n"
    "   **NEVER** use `web_search` / `web_extract` — Tier 1 Google search\n"
    "   uses local debug Chrome: `browser_navigate` to\n"
    "   `https://www.google.com/search?q=...` then `browser_snapshot`, open\n"
    "   promising result URLs the same way. Tier 2 (JS-gated: Instagram /\n"
    "   Linktree / Beacons) = same `browser_*` discipline.\n"
    "3. On hit, report `{found: true, email, source, tier}`. On miss,\n"
    "   report `{found: false, tried: [...]}` and open a\n"
    "   `contact_email_not_found` escalation so the operator sees\n"
    "   exactly which sources were checked.\n"
    "4. Never invent an email address; heuristic guesses are\n"
    "   explicitly forbidden by the skill SOP.\n"
    "\n"
    "## Browser (Tier 1 + Tier 2) — no-hang discipline\n"
    "- Local debug Chrome auto-starts on the first `browser_*` call; do not\n"
    "  open a browser yourself — just call `browser_navigate`.\n"
    "- One page, single attempt. Never retry the same URL. If a navigate or\n"
    "  snapshot errors/times out/returns nothing, record it in `tried` and\n"
    "  move on — never loop on the same call.\n"
    "- Spend the 8-page-load budget then return the miss envelope. A miss is\n"
    "  a valid outcome; a hung run is not. If Chrome cannot be reached or\n"
    "  auto-started, return a miss with reason_hint `browser_unavailable`.\n"
)


def compose_discover_email_brief(
    *,
    identity_id: int,
    handle: str | None,
    env: str,
    campaign_id: str | None,
    actor_email: str,
    gate_b_attempted: bool = False,
    campaign_config_file: str = "",
) -> str:
    """Build the gateway input brief for a single-identity discover run."""
    lines = [
        "# kol_email_discovery",
        f"identity_id: {identity_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
    ]
    if handle:
        lines.append(f"handle: {handle}")
    if campaign_id:
        lines.append(f"campaign_id: {campaign_id}")
    if gate_b_attempted:
        lines.append("gate_b_attempted: true")
    if campaign_config_file:
        lines.append(f"campaign_config_file: {campaign_config_file}")
    lines.extend([
        "",
        "# required_next_step",
        (
            "Resolve the outreach email for the single identity_id "
            "above by invoking `kol-email-discovery`. Do NOT loop "
            "over any other identity. On miss, open a "
            "`contact_email_not_found` escalation with the `tried` "
            "list so the operator can audit which sources you checked."
        ),
    ])
    return "\n".join(lines)


async def _start_email_discover_run(
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
            instructions=DISCOVER_EMAIL_INSTRUCTIONS,
            session_id=session_id,
        )

    try:
        return await launch_or_accept(
            gateway,
            _start,
            session_id=session_id,
            dedup_key=dedup_key,
            kind="email_discover",
            on_success=on_success,
            on_error=on_error,
            job_meta=job_meta,
        )
    except GatewayError as exc:
        raise http_exception_from_gateway_start(
            exc, action_label="搜索邮箱",
        ) from exc


async def dispatch_email_discovery_for_identity(
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
    audit_action: str = "kol.email.discover",
    source: str = "manual",
) -> dict[str, Any]:
    """Queue ``kol-email-discovery`` for one identity (Gate B sync on LIVE).

    Returns a status dict suitable for API responses and approve summaries.
    Raises ``HTTPException`` only when Nox quota is exhausted (manual path).
    On approve, quota exhaustion is reported as ``quota_exhausted`` without
    raising so outreach can proceed for other identities.
    """
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

    existing = str(ident.get("primary_email") or "").strip()
    if existing:
        return {
            "identity_id": identity_id,
            "status": "skipped",
            "reason": "already_has_email",
            "email": existing,
        }

    dedup_key = f"discover-email:{env}:{identity_id}"
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        return {
            "identity_id": identity_id,
            "status": "inflight",
            "run_id": inflight.get("run_id"),
            "started_at": inflight.get("started_at"),
            "handle": handle,
        }

    gate_b_attempted = False
    cfg_path = ""
    if campaign_id and env.upper() == "LIVE":
        facts_resp = None
        try:
            facts_resp = await bridge.read_facts(
                identity_id,
                campaign_id=campaign_id,
                env=env,
            )
        except BridgeError:
            facts_resp = None
        gate_b = await attempt_gate_b_contacts(
            bridge,
            identity_id=identity_id,
            ident=ident,
            campaign_id=campaign_id,
            env=env,
            actor_email=actor_email,
            facts_resp=facts_resp,
        )
        if gate_b.get("quota_exhausted"):
            if source == "approve_shortlist":
                return {
                    "identity_id": identity_id,
                    "status": "quota_exhausted",
                    "handle": handle,
                }
            raise_quota_exhausted(campaign_id=campaign_id, env=env)
        if gate_b.get("email_found"):
            write_audit(
                conn,
                actor_user_id=actor_user_id,
                action=f"{audit_action}.gate_b",
                target=str(identity_id),
                payload={
                    "env": env,
                    "campaign_id": campaign_id,
                    "email": gate_b.get("email"),
                    "cache_hit": gate_b.get("cache_hit"),
                    "source": source,
                },
            )
            return {
                "identity_id": identity_id,
                "status": "gate_b_hit",
                "email": gate_b.get("email"),
                "handle": handle,
            }
        if gate_b.get("gate_b"):
            gate_b_attempted = True
            try:
                cfg = await require_nox_quota_enabled(
                    bridge, campaign_id, env=env,
                )
                cfg_path = materialize_campaign_config_file(
                    campaign_id,
                    cfg,
                    allowed_gates=("pre_outreach_confirm",),
                )
            except Exception:
                cfg_path = ""

    ensure_gateway_bridge_key()
    run_token = new_pending_run_id()
    session_id = email_discover_session_id(env, identity_id, run_token)
    if campaign_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=run_token,
            kind="email_discover",
            session_id=session_id,
            dedup_key=dedup_key,
        )
        conn.commit()

    resolved_handle = handle
    if not resolved_handle:
        ph = ident.get("primary_handle")
        resolved_handle = ph if isinstance(ph, str) else None

    brief = compose_discover_email_brief(
        identity_id=identity_id,
        handle=resolved_handle,
        env=env,
        campaign_id=campaign_id,
        actor_email=actor_email,
        gate_b_attempted=gate_b_attempted,
        campaign_config_file=cfg_path,
    )
    campaign_id_val = campaign_id
    identity_id_val = identity_id
    pending_ref = run_token

    async def _on_discover_error(_exc: Exception) -> None:
        await rollback_discover_email_failure(pending_run_id=pending_ref)

    async def _on_discover_success(run: dict[str, Any], _result: Any) -> None:
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

    accepted, run = await _start_email_discover_run(
        gateway,
        dedup_key=dedup_key,
        brief=brief,
        session_id=session_id,
        on_success=_on_discover_success,
        on_error=_on_discover_error if campaign_id else None,
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
            "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds",
            ),
        }

    run_id = run.get("run_id") if isinstance(run, dict) else None
    if (
        isinstance(run_id, str) and run_id
        and campaign_id
    ):
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


async def dispatch_email_discovery_for_approved_identities(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    selected_rows: list[dict[str, Any]],
    actor_email: str,
    actor_user_id: int,
) -> list[dict[str, Any]]:
    """Queue discover runs for approved identities missing ``primary_email``."""
    results: list[dict[str, Any]] = []
    for row in selected_rows:
        identity_id = int(row["identity_id"])
        outcome = await dispatch_email_discovery_for_identity(
            bridge=bridge,
            gateway=gateway,
            conn=conn,
            identity_id=identity_id,
            env=env,
            campaign_id=campaign_id,
            actor_email=actor_email,
            actor_user_id=actor_user_id,
            handle=row.get("handle"),
            audit_action="kol.email.discover_on_approve",
            source="approve_shortlist",
        )
        if outcome.get("status") not in ("skipped",):
            results.append(outcome)
    return results
