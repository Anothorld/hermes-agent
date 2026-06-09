"""KOL list + detail (merge bridge identities with local notes)."""

from __future__ import annotations

import asyncio
import datetime as _dt
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from ..audit import write_audit
from ..db import _connect
from ..bridge_client import BridgeClient, BridgeError
from ..bridge_runtime import ensure_gateway_bridge_key
from ..campaign_id_norm import CampaignIdNormaliserMixin, norm_campaign_id
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError
from ..gateway_http import http_exception_from_gateway_start
from ..nox_contacts_sync import attempt_gate_b_contacts
from ..nox_diligence_sync import attempt_gate_a_diligence
from ..nox_gate import materialize_campaign_config_file, require_nox_quota_enabled
from ..nox_helpers import dedup_identity_ids_by_nox_creator
from ..nox_quota import assert_nox_quota_available, raise_quota_exhausted
from ..bridge_agent_contract_loader import (
    gateway_contract_for_brief,
    terminal_safety_rules,
)
from ..background_jobs import create_job, get_job, run_in_background
from ..launch_accept import launch_or_accept
from ..launch_rollback import rollback_discover_email_failure
from ..run_launch_queue import new_pending_run_id
from ..run_registry import (
    INFLIGHT_TTL_SECONDS,
    finalize_run_id,
    get_inflight_run,
    register_run,
)

router = APIRouter(prefix="/kols", tags=["kols"])

_REPO_ROOT = str(Path(__file__).resolve().parents[5])


async def _start_detached_gateway_run(
    gateway: GatewayClient,
    *,
    action_label: str,
    dedup_key: str | None = None,
    on_success: Any = None,
    job_meta: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[bool, dict[str, Any]]:
    """Start a run via launch queue; may return 202-shaped accept body."""
    session_id = str(kwargs.get("session_id") or "")
    kind = None
    if session_id.startswith("kol-email-discover:"):
        kind = "email_discover"
    elif ":recovery-" in session_id:
        kind = "recovery"

    async def _start() -> dict[str, Any]:
        return await gateway.start_run_with_retry(**kwargs)

    try:
        return await launch_or_accept(
            gateway,
            _start,
            session_id=session_id or f"detached:{action_label}",
            dedup_key=dedup_key,
            kind=kind,
            on_success=on_success,
            job_meta=job_meta,
        )
    except GatewayError as exc:
        raise http_exception_from_gateway_start(
            exc, action_label=action_label,
        ) from exc


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


@router.get("/archive")
async def list_archived_kols(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    last_outcome: str | None = Query(None, max_length=60),
    platform: str | None = Query(None, max_length=40),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Cross-campaign KOL pool: identities with ≥1 archived collab.

    Powers the past-collab browser at ``/kols/archive`` in the UI.
    Returns ``{total, limit, offset, items, env}``.
    """
    try:
        return await bridge.list_archived_kols(
            env=_env(env), q=q, last_outcome=last_outcome,
            platform=platform, limit=limit, offset=offset,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/jobs/{job_id}")
async def get_kol_job(
    job_id: str,
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    row = get_job(job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return row


@router.get("/{identity_id}")
async def get_kol(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    try:
        identity = await bridge.get_identity(identity_id, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    notes = conn.execute(
        "SELECT id, body, author_user_id, created_at FROM kol_notes "
        "WHERE kol_identity_id=? ORDER BY created_at DESC",
        (identity_id,),
    ).fetchall()
    identity["notes"] = [dict(n) for n in notes]
    return identity


@router.get("/{identity_id}/timeline")
async def get_timeline(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
    campaign_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    resolved_env = _env(env)
    try:
        events = await bridge.get_timeline(
            identity_id,
            env=resolved_env,
            campaign_id=campaign_id,
            limit=limit,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": resolved_env,
        "events": events,
    }


@router.get("/{identity_id}/communication-history")
async def get_communication_history(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(current_user)],
    campaign_id: str = Query(..., min_length=1),
    env: str | None = Query(None),
) -> dict:
    """Gmail sent/received email rows for the KOL detail communication panel."""
    resolved_cid = norm_campaign_id(campaign_id)
    if not resolved_cid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "invalid_campaign_id",
                "message": "campaign_id is required and must not be a null/undefined sentinel",
            },
        )
    resolved_env = _env(env)
    try:
        return await bridge.get_email_conversation(
            identity_id,
            resolved_cid,
            env=resolved_env,
            operator_user_id=int(user["id"]),
        )
    except BridgeError as exc:
        if exc.status == 403:
            raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
        if exc.status == 409:
            raise HTTPException(status.HTTP_409_CONFLICT, exc.detail) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class TakeoverMailboxBody(BaseModel):
    campaign_id: str = Field(min_length=1)
    env: str | None = None


@router.post("/{identity_id}/takeover-mailbox")
async def takeover_mailbox(
    identity_id: int,
    body: TakeoverMailboxBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
) -> dict:
    """Reassign campaign Gmail mailbox to the current operator."""
    from ..gmail_store import get_connection

    if not get_connection(conn, user["id"], active_only=True):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "gmail_not_connected",
                "message": "Connect Gmail in Settings before taking over a mailbox",
            },
        )
    resolved_cid = norm_campaign_id(body.campaign_id)
    if not resolved_cid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": "invalid_campaign_id", "message": "campaign_id is required"},
        )
    resolved_env = _env(body.env)
    try:
        out = await bridge.takeover_mailbox(
            identity_id,
            {
                "campaign_id": resolved_cid,
                "env": resolved_env,
                "operator_user_id": user["id"],
                "operator_email": user["email"],
                "requester_role": user["role"],
            },
            operator_user_id=int(user["id"]),
        )
    except BridgeError as exc:
        if exc.status == 403:
            raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
        if exc.status == 409:
            raise HTTPException(status.HTTP_409_CONFLICT, exc.detail) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.mailbox.takeover",
        target=str(identity_id),
        payload={"campaign_id": resolved_cid, "env": resolved_env},
    )
    return out


class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/{identity_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(
    identity_id: int,
    body: NoteBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    conn.execute(
        "INSERT INTO kol_notes (kol_identity_id, author_user_id, body, created_at) VALUES (?,?,?,?)",
        (identity_id, user["id"], body.body,
         _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
    )
    write_audit(conn, actor_user_id=user["id"], action="kol.note.add", target=str(identity_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Email enrichment — manual entry + skill-driven discovery
# ---------------------------------------------------------------------------
#
# The detail page surfaces these two paths when a KOL has no
# ``primary_email``. The cold-outreach skill won't draft for an
# identity without a verified email (see
# campaigns.py:_APPROVAL_INSTRUCTIONS step 4), so the kanban "draft
# missing" state is often really an "email missing" state. Giving the
# operator a one-click manual fill + a one-click "ask the web" button
# is the shortest path back to an unblocked draft.


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SetEmailBody(CampaignIdNormaliserMixin):
    """Body for ``POST /kols/{identity_id}/email``.

    ``email`` is the new primary_email value. ``campaign_id`` is
    optional and only used as provenance on the audit + facts row.
    ``override_existing`` is the explicit ack that a previously set
    email will be overwritten — without it, the endpoint refuses if
    primary_email is already non-empty. (Discovery skill writes are
    idempotent in their own check; this guard is for the operator
    manually editing.)
    """

    email: EmailStr
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None
    override_existing: bool = False


@router.post("/{identity_id}/email")
async def set_primary_email(
    identity_id: int,
    body: SetEmailBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Operator-driven manual fill of ``identity.primary_email``.

    Writes both the identity row (so the cold-outreach skill's
    ``primary_email`` check passes) and the matching provenance facts
    (``identity.email``, ``identity.email_source=manual``,
    ``identity.email_discovered_at``) so the audit trail shows where
    the value came from.

    Concurrency:
    * 409 ``email_already_set`` if a non-empty primary_email exists and
      ``override_existing`` is false — keeps a misclick from silently
      stomping a previously verified address.
    * Always last-writer-wins on the actual write; an in-flight
      discovery skill that finishes after this point will see the
      non-empty primary_email and skip its own write (per skill SOP).
    """
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "email format looks invalid",
        )

    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")
    existing = str(ident.get("primary_email") or "").strip()
    if existing and not body.override_existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "email_already_set",
                "message": (
                    "this KOL already has a primary_email on file. "
                    "Re-submit with override_existing=true if you "
                    "really want to replace it."
                ),
                "current_email": existing,
            },
        )

    handle = ident.get("primary_handle")
    if not handle:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "identity has no primary_handle on file — refusing to upsert",
        )
    payload: dict[str, Any] = {
        "primary_handle": handle,
        "platform": ident.get("platform") or "instagram",
        "primary_email": email,
        "env": body.env,
    }
    try:
        await bridge.upsert_identity(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": {
                    "identity.email": email,
                    "identity.email_source": "manual",
                    "identity.email_discovered_at": now_iso,
                    "identity.email_set_by": user["email"],
                },
                "source": f"web:{user['email']}",
                "env": body.env,
                "campaign_id": body.campaign_id,
            },
        )
    except BridgeError as exc:
        # primary_email is already in the identity row at this point;
        # don't fail the whole call just because provenance facts
        # couldn't be persisted. Surface as a 207-style payload.
        write_audit(
            conn,
            actor_user_id=user["id"],
            action="kol.email.set_partial",
            target=str(identity_id),
            payload={"email": email, "facts_error": str(exc)},
        )
        return {
            "ok": True,
            "email": email,
            "warning": f"identity updated but provenance facts failed: {exc}",
        }

    # Timeline event so the detail page shows the operator action.
    try:
        await bridge.write_event(
            {
                "identity_id": identity_id,
                "campaign_id": body.campaign_id,
                "event_type": "identity.email_set_manual",
                "actor": f"web:{user['email']}",
                "payload": {"email": email, "previous_email": existing or None},
                "env": body.env,
            },
        )
    except BridgeError:
        # Audit-only event; don't surface bridge errors here.
        pass

    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.email.set",
        target=str(identity_id),
        payload={"email": email, "override": bool(existing)},
    )
    return {"ok": True, "email": email, "previous_email": existing or None}


class DiscoverEmailBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None


_DISCOVER_EMAIL_INSTRUCTIONS = (
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


def _compose_discover_email_brief(
    *,
    identity_id: int,
    handle: str | None,
    env: str,
    campaign_id: str | None,
    actor_email: str,
    gate_b_attempted: bool = False,
    campaign_config_file: str = "",
) -> str:
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


@router.post("/{identity_id}/discover-email")
async def discover_email(
    identity_id: int,
    body: DiscoverEmailBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Kick off the kol-email-discovery skill for one identity.

    Async — the gateway run takes 30–120 s while the agent crawls
    public surfaces (link-in-bio, personal site, media kit). The
    resulting ``primary_email`` and provenance facts land in CAL when
    the run completes; the detail page's poll loop will surface them
    on the next refresh.

    Concurrency:
    * 409 ``already_has_email`` if the identity already has a
      non-empty primary_email. (The skill itself short-circuits the
      same way; we just save the round-trip.)
    * 409 ``discover_email_inflight`` if another discovery was fired
      for this (identity, env) in the last ``INFLIGHT_TTL_SECONDS`` —
      durable across page reloads via product_campaign_runs.
    """
    env = body.env
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    existing = str(ident.get("primary_email") or "").strip()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "already_has_email",
                "message": (
                    "this KOL already has a primary_email on file. "
                    "Edit it manually instead of re-running discovery."
                ),
                "current_email": existing,
            },
        )

    # Per-identity TTL dedup. We intentionally don't gate on
    # ``_campaign_run_in_flight`` here — discovery is a single-identity
    # web-crawl run, not a campaign-mutating run, and blocking it
    # behind every approve cycle would feel unresponsive.
    dedup_key = f"discover-email:{env}:{identity_id}"
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "discover_email_inflight",
                "message": (
                    f"an email discovery run for this KOL was triggered "
                    f"in the last {INFLIGHT_TTL_SECONDS}s — wait for "
                    f"it to land (typically 30–120s) before retrying"
                ),
                "run_id": inflight.get("run_id"),
                "started_at": inflight.get("started_at"),
            },
        )

    gate_b_attempted = False
    cfg_path = ""
    if body.campaign_id and env.upper() == "LIVE":
        facts_resp = None
        try:
            facts_resp = await bridge.read_facts(
                identity_id,
                campaign_id=body.campaign_id,
                env=env,
            )
        except BridgeError:
            facts_resp = None
        gate_b = await attempt_gate_b_contacts(
            bridge,
            identity_id=identity_id,
            ident=ident,
            campaign_id=body.campaign_id,
            env=env,
            actor_email=user["email"],
            facts_resp=facts_resp,
        )
        if gate_b.get("quota_exhausted"):
            raise_quota_exhausted(campaign_id=body.campaign_id, env=env)
        if gate_b.get("email_found"):
            write_audit(
                conn,
                actor_user_id=user["id"],
                action="kol.email.discover_gate_b",
                target=str(identity_id),
                payload={
                    "env": env,
                    "campaign_id": body.campaign_id,
                    "email": gate_b.get("email"),
                    "cache_hit": gate_b.get("cache_hit"),
                },
            )
            return {
                "ok": True,
                "gate_b": True,
                "skipped_browser_discover": True,
                "email": gate_b.get("email"),
                "identity_id": identity_id,
            }
        if gate_b.get("gate_b"):
            gate_b_attempted = True
            try:
                cfg = await require_nox_quota_enabled(
                    bridge, body.campaign_id, env=env,
                )
                cfg_path = materialize_campaign_config_file(
                    body.campaign_id,
                    cfg,
                    allowed_gates=("pre_outreach_confirm",),
                )
            except HTTPException:
                cfg_path = ""

    ensure_gateway_bridge_key()
    pending_run_id: str | None = None
    if body.campaign_id:
        pending_run_id = new_pending_run_id()
        register_run(
            conn,
            campaign_id=body.campaign_id,
            env=env,
            run_id=pending_run_id,
            kind="draft",
            session_id=f"kol-email-discover:{env}:{identity_id}",
            dedup_key=dedup_key,
        )
        conn.commit()
    handle = ident.get("primary_handle")
    brief = _compose_discover_email_brief(
        identity_id=identity_id,
        handle=handle if isinstance(handle, str) else None,
        env=env,
        campaign_id=body.campaign_id,
        actor_email=user["email"],
        gate_b_attempted=gate_b_attempted,
        campaign_config_file=cfg_path,
    )
    campaign_id_val = body.campaign_id
    identity_id_val = identity_id
    pending_ref = pending_run_id
    user_id = user["id"]

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
                actor_user_id=user_id,
                action="kol.email.discover",
                target=str(identity_id_val),
                payload={
                    "env": env,
                    "campaign_id": campaign_id_val,
                    "run_id": rid,
                    "async_accept": True,
                },
            )
            bg.commit()
        finally:
            bg.close()

    accepted, run = await _start_detached_gateway_run(
        gateway,
        action_label="搜索邮箱",
        dedup_key=dedup_key,
        input=brief,
        instructions=_DISCOVER_EMAIL_INSTRUCTIONS,
        session_id=f"kol-email-discover:{env}:{identity_id}",
        on_success=_on_discover_success,
        on_error=_on_discover_error if pending_run_id else None,
        job_meta={
            "identity_id": identity_id,
            "campaign_id": body.campaign_id,
            "env": env,
        },
    )
    if accepted:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                **run,
                "ok": True,
                "identity_id": identity_id,
                "pending_run_id": pending_run_id,
                "started_at": _dt.datetime.now(
                    _dt.timezone.utc,
                ).isoformat(timespec="seconds"),
            },
        )

    run_id = run.get("run_id") if isinstance(run, dict) else None
    if (
        isinstance(run_id, str) and run_id
        and body.campaign_id and pending_run_id
    ):
        finalize_run_id(conn, pending_run_id=pending_run_id, actual_run_id=run_id)
        conn.commit()
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.email.discover",
        target=str(identity_id),
        payload={"env": env, "campaign_id": body.campaign_id, "run_id": run_id},
    )
    resp: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "identity_id": identity_id,
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    if isinstance(run, dict):
        if run.get("_queued"):
            resp["queued"] = True
            resp["waited_sec"] = run.get("_waited_sec")
        if run.get("_queue_position"):
            resp["queue_position"] = run.get("_queue_position")
    return resp


# ---------------------------------------------------------------------------
# Nox API gates (quota + monthly cache via nox_kol_tool.py)
# ---------------------------------------------------------------------------


class NoxDiligenceBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None


class NoxDiligenceBatchBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str = Field(min_length=1)
    identity_ids: list[int] = Field(min_length=1, max_length=50)


class NoxContactsBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None


@router.post("/{identity_id}/nox-diligence")
async def nox_diligence(
    identity_id: int,
    body: NoxDiligenceBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Gate A: synchronous Nox diligence-pack + deterministic CAL fact hydration."""
    env = body.env
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    if env.upper() == "LIVE":
        if not body.campaign_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"code": "campaign_id_required", "detail": "campaign_id required for LIVE Nox diligence"},
            )
        await require_nox_quota_enabled(bridge, body.campaign_id, env=env)

    campaign_id = body.campaign_id or ""
    facts_resp = None
    if campaign_id:
        try:
            facts_resp = await bridge.read_facts(
                identity_id,
                campaign_id=campaign_id,
                env=env,
            )
        except BridgeError:
            facts_resp = None

    result = await attempt_gate_a_diligence(
        bridge,
        identity_id=identity_id,
        ident=ident,
        campaign_id=campaign_id or "TEST",
        env=env,
        actor_email=user["email"],
        facts_resp=facts_resp,
    )
    if result.get("quota_exhausted"):
        raise_quota_exhausted(campaign_id=campaign_id or None, env=env)
    if result.get("skipped"):
        reason = result.get("reason") or "diligence_skipped"
        if reason == "nox_quota_disabled":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"code": "nox_quota_disabled", "detail": result.get("detail") or reason},
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": reason, "detail": result.get("detail") or reason},
        )

    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.nox.diligence",
        target=str(identity_id),
        payload={
            "env": env,
            "campaign_id": body.campaign_id,
            "sync": True,
            "cache_hit": result.get("cache_hit"),
            "facts_written": result.get("facts_written"),
            "verdict": result.get("verdict"),
        },
    )
    return {
        "ok": True,
        "identity_id": identity_id,
        "sync": True,
        "cache_hit": result.get("cache_hit"),
        "cache_month": result.get("cache_month"),
        "api_calls": result.get("api_calls"),
        "facts_written": result.get("facts_written"),
        "fact_keys": result.get("fact_keys"),
        "verdict": result.get("verdict"),
    }


async def _execute_nox_diligence_batch(
    *,
    bridge: BridgeClient,
    conn: sqlite3.Connection,
    user: dict,
    campaign_id: str,
    env: str,
    ids: list[int],
    dropped_dupes: list[int],
    sync: bool,
) -> dict[str, Any]:
    """Run Gate A diligence for each identity (parallel, bounded)."""
    if env.upper() == "LIVE":
        await require_nox_quota_enabled(bridge, campaign_id, env=env)
        await assert_nox_quota_available(bridge, campaign_id, env=env)

    brief_map = await bridge.batch_identity_briefs(ids)
    sem = asyncio.Semaphore(get_settings().nox_max_concurrent)
    processed: list[dict] = []
    errors: list[dict] = []
    quota_hit = False

    async def _process_one(iid: int) -> None:
        nonlocal quota_hit
        async with sem:
            ident = brief_map.get(iid) or {}
            if not ident:
                errors.append({"identity_id": iid, "error": "not_found"})
                return
            facts_resp = None
            try:
                facts_resp = await bridge.read_facts(
                    iid, campaign_id=campaign_id, env=env,
                )
            except BridgeError:
                facts_resp = None
            result = await attempt_gate_a_diligence(
                bridge,
                identity_id=iid,
                ident=ident,
                campaign_id=campaign_id,
                env=env,
                actor_email=user["email"],
                facts_resp=facts_resp,
            )
            if result.get("quota_exhausted"):
                quota_hit = True
                return
            if result.get("skipped") or not result.get("ok"):
                errors.append({
                    "identity_id": iid,
                    "reason": result.get("reason"),
                    "detail": result.get("detail"),
                })
                return
            processed.append({
                "identity_id": iid,
                "verdict": result.get("verdict"),
                "cache_hit": result.get("cache_hit"),
                "facts_written": result.get("facts_written"),
            })

    await asyncio.gather(*[_process_one(iid) for iid in ids])
    if quota_hit:
        raise_quota_exhausted(campaign_id=campaign_id, env=env)

    payload = {
        "identity_ids": ids,
        "dropped_identity_ids": dropped_dupes,
        "processed": processed,
        "errors": errors,
        "sync": sync,
    }
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.nox.diligence_batch",
        target=campaign_id,
        payload=payload,
    )
    return {
        "ok": True,
        "sync": sync,
        "identity_ids": ids,
        "dropped_identity_ids": dropped_dupes,
        "processed": processed,
        "errors": errors,
        "processed_count": len(processed),
        "error_count": len(errors),
    }


@router.post("/nox-diligence-batch")
async def nox_diligence_batch(
    body: NoxDiligenceBatchBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Gate A batch: parallel diligence-pack per identity."""
    env = body.env
    ids = list(dict.fromkeys(body.identity_ids))
    dropped_dupes: list[int] = []
    if env.upper() == "LIVE" and ids:
        ids, dropped_dupes = await dedup_identity_ids_by_nox_creator(
            bridge,
            ids,
            campaign_id=body.campaign_id,
            env=env,
        )
    if not ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "nox_diligence_batch_empty",
                "detail": "all identity_ids deduplicated by nox_creator_id",
                "dropped_identity_ids": dropped_dupes,
            },
        )

    settings = get_settings()
    use_async = (
        settings.nox_batch_async
        and len(ids) >= settings.nox_batch_async_min_ids
    )
    if use_async:
        campaign_id = body.campaign_id
        actor = dict(user)
        job_id = create_job(
            kind="nox-diligence-batch",
            meta={
                "campaign_id": campaign_id,
                "env": env,
                "identity_count": len(ids),
            },
        )

        async def _runner() -> dict[str, Any]:
            bg_conn = _connect(get_settings().db_path)
            try:
                return await _execute_nox_diligence_batch(
                    bridge=bridge,
                    conn=bg_conn,
                    user=actor,
                    campaign_id=campaign_id,
                    env=env,
                    ids=ids,
                    dropped_dupes=dropped_dupes,
                    sync=False,
                )
            finally:
                bg_conn.close()

        await run_in_background(job_id, _runner)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "status": "accepted",
                "poll": f"/kols/jobs/{job_id}",
                "identity_count": len(ids),
            },
        )

    return await _execute_nox_diligence_batch(
        bridge=bridge,
        conn=conn,
        user=user,
        campaign_id=body.campaign_id,
        env=env,
        ids=ids,
        dropped_dupes=dropped_dupes,
        sync=True,
    )


@router.post("/{identity_id}/nox-contacts")
async def nox_contacts(
    identity_id: int,
    body: NoxContactsBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Gate B: deterministic Nox contacts (``nox_kol_tool.py``) before browser discovery."""
    env = body.env
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")
    if str(ident.get("primary_email") or "").strip():
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_has_email",
            "primary_email": ident.get("primary_email"),
        }
    if env.upper() == "LIVE" and not body.campaign_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": "campaign_id_required", "detail": "campaign_id required for LIVE Nox contacts"},
        )
    if env.upper() == "LIVE" and body.campaign_id:
        await require_nox_quota_enabled(bridge, body.campaign_id, env=env)
        await assert_nox_quota_available(bridge, body.campaign_id, env=env)

    facts_resp = None
    if body.campaign_id:
        try:
            facts_resp = await bridge.read_facts(
                identity_id,
                campaign_id=body.campaign_id,
                env=env,
            )
        except BridgeError:
            facts_resp = None

    gate_b: dict[str, Any] = {"skipped": True, "reason": "no_campaign_id"}
    if body.campaign_id:
        gate_b = await attempt_gate_b_contacts(
            bridge,
            identity_id=identity_id,
            ident=ident,
            campaign_id=body.campaign_id,
            env=env,
            actor_email=user["email"],
            facts_resp=facts_resp,
        )
    if gate_b.get("quota_exhausted"):
        raise_quota_exhausted(campaign_id=body.campaign_id or "", env=env)

    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.nox.contacts",
        target=str(identity_id),
        payload={"env": env, "campaign_id": body.campaign_id, "gate_b": gate_b},
    )
    if gate_b.get("email_found"):
        return {
            "ok": True,
            "identity_id": identity_id,
            "email_found": True,
            "email": gate_b.get("email"),
            "cache_hit": gate_b.get("cache_hit"),
        }
    return {
        "ok": True,
        "identity_id": identity_id,
        "email_found": False,
        "gate_b": gate_b,
    }


class NoxMonitorBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None
    video_url: str = Field(min_length=10)


_NOX_MONITOR_INSTRUCTIONS = (
    "You are running `kol-nox-monitor` for ONE published video URL.\n"
    f"- Repo root: {_REPO_ROOT}\n"
    f"- Nox: python {_REPO_ROOT}/plugins/nox-kol-bridge/scripts/nox_kol_tool.py\n"
    "1. LIVE: `--campaign-config-file` on all `nox_kol_tool.py` calls.\n"
    "2. `skill_view(name='kol-nox-monitor')` — dry-run then `--force` after confirm.\n"
    "3. No monitor history polling.\n"
)


@router.post("/{identity_id}/nox-monitor")
async def nox_monitor(
    identity_id: int,
    body: NoxMonitorBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Gate C: register video in Nox monitor (one-shot)."""
    ensure_gateway_bridge_key()
    cfg_path = ""
    if body.env.upper() == "LIVE":
        if not body.campaign_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"code": "campaign_id_required", "detail": "campaign_id required for LIVE Nox monitor"},
            )
        cfg = await require_nox_quota_enabled(bridge, body.campaign_id, env=body.env)
        cfg_path = materialize_campaign_config_file(
            body.campaign_id,
            cfg,
            allowed_gates=("post_publish_confirm",),
        )
    brief = "\n".join([
        "# kol_nox_monitor",
        f"identity_id: {identity_id}",
        f"video_url: {body.video_url}",
        f"mode: {body.env}",
        f"campaign_id: {body.campaign_id or ''}",
        f"campaign_config_file: {cfg_path}",
    ])
    accepted, run = await _start_detached_gateway_run(
        gateway,
        action_label="启用 Nox 监测",
        input=brief,
        instructions=_NOX_MONITOR_INSTRUCTIONS,
        session_id=f"kol-nox-monitor:{body.env}:{identity_id}",
    )
    if accepted:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={**run, "ok": True, "identity_id": identity_id},
        )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.nox.monitor",
        target=str(identity_id),
        payload={"video_url": body.video_url, "run_id": run_id},
    )
    return {"ok": True, "run_id": run_id}


# ---------------------------------------------------------------------------
# Social-link enrichment — manual entry + skill-driven discovery
# ---------------------------------------------------------------------------
#
# Cross-platform profile URLs (TikTok / YouTube / Facebook / X / Threads /
# Linktree / personal site) drive the "快速跳转" bar on the detail page.
# IG is normally captured during instagram-kol-discovery; the others are
# either side-effects of kol-email-discovery (when it browses link-in-bio
# pages) or filled by the operator / the dedicated discovery skill below.

ALLOWED_SOCIAL_LINK_KEYS: tuple[str, ...] = (
    "identity.instagram_profile_url",
    "identity.tiktok_profile_url",
    "identity.youtube_profile_url",
    "identity.facebook_profile_url",
    "identity.twitter_profile_url",
    "identity.threads_profile_url",
    "identity.linktree_url",
    "identity.personal_site_url",
)

_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.IGNORECASE)


class SetSocialLinkBody(CampaignIdNormaliserMixin):
    """Body for ``POST /kols/{identity_id}/social-link``.

    ``fact_key`` must be one of ``ALLOWED_SOCIAL_LINK_KEYS`` — keeps the
    endpoint from being a generic identity-namespace fact writer (which
    would belong to FactsEditor, not here).
    """

    fact_key: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=4, max_length=2000)
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None
    override_existing: bool = False


@router.post("/{identity_id}/social-link")
async def set_social_link(
    identity_id: int,
    body: SetSocialLinkBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Operator-driven manual fill of a single social-platform URL.

    Writes the URL fact + provenance (``<key>_source=manual``,
    ``<key>_discovered_at``) so the detail page shows where the value
    came from. Refuses to overwrite a non-empty existing value unless
    ``override_existing`` is true — symmetric with ``set_primary_email``.
    """
    if body.fact_key not in ALLOWED_SOCIAL_LINK_KEYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "unknown_social_key",
                "message": f"fact_key must be one of {ALLOWED_SOCIAL_LINK_KEYS}",
            },
        )
    url = body.url.strip()
    if not _URL_RE.match(url):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "url must start with http:// or https:// and contain no whitespace",
        )

    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    # Check existing fact (campaign_id=None for reusable scope so the
    # check matches the write below).
    try:
        existing_facts = await bridge.read_facts(
            identity_id, campaign_id=None, env=body.env,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    facts_map = (existing_facts or {}).get("facts") or {}
    existing_url = str(facts_map.get(body.fact_key) or "").strip()
    if existing_url and not body.override_existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "social_link_already_set",
                "message": (
                    f"{body.fact_key} is already set. Re-submit with "
                    "override_existing=true to replace it."
                ),
                "current_url": existing_url,
            },
        )

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    source_key = f"{body.fact_key}_source"
    at_key = f"{body.fact_key}_discovered_at"
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": {
                    body.fact_key: url,
                    source_key: "manual",
                    at_key: now_iso,
                },
                "source": f"web:{user['email']}",
                "env": body.env,
                # campaign_id=None makes this a reusable identity fact —
                # the next campaign for this KOL will inherit the URL.
                "campaign_id": None,
            },
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.social_link.set",
        target=str(identity_id),
        payload={
            "fact_key": body.fact_key,
            "url": url,
            "override": bool(existing_url),
        },
    )
    return {"ok": True, "fact_key": body.fact_key, "url": url}


class DiscoverSocialLinksBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None


_DISCOVER_SOCIAL_LINKS_INSTRUCTIONS = (
    "You are running the `kol-social-link-discovery` skill for ONE\n"
    "specific KOL identity at the request of the web console operator.\n"
    "Do NOT loop over any other identity in this campaign.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    f"{gateway_contract_for_brief(compact=True)}\n"
    f"{terminal_safety_rules(repo_root=_REPO_ROOT)}\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- CLI failures print JSON on **stdout**. Empty output + exit 2 → read\n"
    "  stdout for `error`/`hint`; never fall back to execute_code.\n"
    "\n"
    "## Pipeline\n"
    "1. Read the identity row and the current identity-namespace facts.\n"
    "   For each target fact key in {instagram, tiktok, youtube, facebook,\n"
    "   twitter, threads, linktree, personal_site}_profile_url that is\n"
    "   already non-empty, treat it as resolved and DO NOT overwrite.\n"
    "2. If every target key is already resolved, stop immediately and\n"
    "   report `{skipped: \"already_has_all_social_links\"}`.\n"
    "3. Invoke `kol-social-link-discovery` with the supplied identity_id,\n"
    "   env, and campaign_id. The skill resolves missing URLs from public\n"
    "   sources (link-in-bio, personal site, IG/Facebook bio, media kit)\n"
    "   and writes each newly resolved URL plus its provenance facts via\n"
    "   `write-facts-multi` with campaign_id=null.\n"
    "   Tier 1/2 use local debug Chrome via `browser_*` only — never\n"
    "   `web_search`/`web_extract` or terminal HTTP scraping.\n"
    "4. On hit, report `{found: true, resolved: [...]}`. On total miss,\n"
    "   report `{found: false, tried: [...]}`. Do NOT open an escalation\n"
    "   — missing social URLs are non-blocking for outreach.\n"
    "5. Never invent URLs; heuristic guesses (e.g. instagram.com/handle\n"
    "   when you never verified the page belongs to the creator) are\n"
    "   forbidden by the skill SOP.\n"
    "\n"
    "## Browser (Tier 1 + Tier 2) — local Chrome, no-hang discipline\n"
    "- One page, single attempt. Never retry the same URL.\n"
    "- Navigate/snapshot error or timeout → record in `tried` and move on.\n"
    "- A miss is valid; a hung run is not. Return `browser_unavailable` on\n"
    "  Chrome auto-start failure — do not loop.\n"
)


def _compose_discover_social_links_brief(
    *,
    identity_id: int,
    handle: str | None,
    env: str,
    campaign_id: str | None,
    actor_email: str,
) -> str:
    lines = [
        "# kol_social_link_discovery",
        f"identity_id: {identity_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
    ]
    if handle:
        lines.append(f"handle: {handle}")
    if campaign_id:
        lines.append(f"campaign_id: {campaign_id}")
    lines.extend([
        "",
        "# required_next_step",
        (
            "Resolve the missing social-platform profile URLs for the "
            "single identity_id above by invoking "
            "`kol-social-link-discovery`. Do NOT loop over any other "
            "identity. Skip any target key that is already non-empty. "
            "On total miss, return the `tried` list — do NOT escalate."
        ),
    ])
    return "\n".join(lines)


@router.post("/{identity_id}/discover-social-links")
async def discover_social_links(
    identity_id: int,
    body: DiscoverSocialLinksBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Kick off the kol-social-link-discovery skill for one identity.

    Async — the gateway run takes 30–120s while the agent crawls public
    surfaces. New URL facts land in CAL when the run completes; the
    detail page's poll loop surfaces them on the next refresh.

    Concurrency:
    * 409 ``discover_social_links_inflight`` if another discovery was
      fired for this (identity, env) in the last ``INFLIGHT_TTL_SECONDS``.
    * Unlike ``discover_email``, no "already has X" 409 — the skill
      short-circuits internally when all target keys are filled, and an
      operator may legitimately want to re-run to pick up a new platform
      they expect to exist.
    """
    env = body.env
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    dedup_key = f"discover-social-links:{env}:{identity_id}"
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "discover_social_links_inflight",
                "message": (
                    f"a social-link discovery run for this KOL was triggered "
                    f"in the last {INFLIGHT_TTL_SECONDS}s — wait for it to "
                    f"land (typically 30–120s) before retrying"
                ),
                "run_id": inflight.get("run_id"),
                "started_at": inflight.get("started_at"),
            },
        )

    ensure_gateway_bridge_key()
    handle = ident.get("primary_handle")
    brief = _compose_discover_social_links_brief(
        identity_id=identity_id,
        handle=handle if isinstance(handle, str) else None,
        env=env,
        campaign_id=body.campaign_id,
        actor_email=user["email"],
    )
    accepted, run = await _start_detached_gateway_run(
        gateway,
        action_label="搜索社交链接",
        input=brief,
        instructions=_DISCOVER_SOCIAL_LINKS_INSTRUCTIONS,
        session_id=f"kol-social-link-discover:{env}:{identity_id}",
    )
    if accepted:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={**run, "ok": True, "identity_id": identity_id},
        )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id and body.campaign_id:
        register_run(
            conn,
            campaign_id=body.campaign_id,
            env=env,
            run_id=run_id,
            kind="draft",
            session_id=f"kol-social-link-discover:{env}:{identity_id}",
            dedup_key=dedup_key,
        )
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.social_links.discover",
        target=str(identity_id),
        payload={"env": env, "campaign_id": body.campaign_id, "run_id": run_id},
    )
    return {
        "ok": True,
        "run_id": run_id,
        "identity_id": identity_id,
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
