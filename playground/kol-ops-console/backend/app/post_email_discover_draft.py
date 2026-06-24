"""Auto-trigger initial outreach draft after approve-time enrichment runs."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import sqlite3

from .audit import write_audit
from .bridge_client import BridgeClient, BridgeError
from .bridge_runtime import BRIDGE_KEY_ENV, resolve_bridge_key
from .bridge_agent_contract_loader import slim_dispatch_context_for_agent
from .campaign_config_sync import DEFAULT_REQUIRED_FIELDS
from .gateway_client import GatewayClient, GatewayError
from .run_registry import get_inflight_run, register_run
from .session_ids import campaign_draft_session_id

log = logging.getLogger(__name__)

_SYSTEM_ACTOR_EMAIL = "system:post_enrichment_auto_draft"


def parse_email_discover_session(session_id: str) -> tuple[str, int] | None:
    """Parse ``kol-email-discover:{env}:{identity_id}:{run_token}``."""
    if not session_id.startswith("kol-email-discover:"):
        return None
    parts = session_id.split(":")
    if len(parts) < 4:
        return None
    env, identity_raw = parts[1], parts[2]
    try:
        return env, int(identity_raw)
    except ValueError:
        return None


def parse_creator_brief_refresh_session(session_id: str) -> tuple[str, int] | None:
    """Parse ``kol-creator-brief-refresh:{env}:{identity_id}:{run_token}``."""
    if not session_id.startswith("kol-creator-brief-refresh:"):
        return None
    parts = session_id.split(":")
    if len(parts) < 4:
        return None
    env, identity_raw = parts[1], parts[2]
    try:
        return env, int(identity_raw)
    except ValueError:
        return None


def _resolve_approve_actor(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
) -> tuple[str, int | None]:
    """Best-effort lookup of the operator who approved the shortlist."""
    rows = conn.execute(
        """SELECT actor_user_id, payload_json FROM audit_log
            WHERE action='campaign.approve_shortlist' AND target=?
            ORDER BY ts DESC LIMIT 10""",
        (campaign_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("env") != env:
            continue
        user_id = row["actor_user_id"]
        if isinstance(user_id, int):
            email_row = conn.execute(
                "SELECT email FROM users WHERE id=?", (user_id,),
            ).fetchone()
            if email_row and email_row["email"]:
                return str(email_row["email"]), user_id
        return _SYSTEM_ACTOR_EMAIL, user_id if isinstance(user_id, int) else None
    return _SYSTEM_ACTOR_EMAIL, None


def _draft_already_exists(facts: dict[str, Any]) -> bool:
    if facts.get("offer.outreach_draft_ready"):
        return True
    draft = facts.get("approval.reply_draft")
    if isinstance(draft, dict) and draft.get("decision") in ("pending", "approved"):
        return True
    return False


async def _campaign_config_ready(
    bridge: BridgeClient,
    campaign_id: str,
) -> bool:
    try:
        config = await bridge.get_campaign(campaign_id)
    except BridgeError:
        return False
    if not isinstance(config, dict):
        return False
    for field in DEFAULT_REQUIRED_FIELDS:
        value = config.get(field)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


async def _is_approved_candidate(
    bridge: BridgeClient,
    *,
    campaign_id: str,
    identity_id: int,
    env: str,
) -> bool:
    try:
        candidates = await bridge.list_candidates(campaign_id, env=env)
    except BridgeError:
        return False
    for row in candidates:
        if row.get("identity_id") == identity_id:
            return row.get("candidate_status") == "selected_for_outreach"
    return False


def _ensure_bridge_key_for_gateway() -> bool:
    key = resolve_bridge_key()
    if not key:
        log.warning("post_enrichment_auto_draft: bridge key missing; skip auto-draft")
        return False
    os.environ[BRIDGE_KEY_ENV] = key
    os.environ.setdefault("KOC_BRIDGE_KEY", key)
    return True


async def _creator_brief_ready(
    bridge: BridgeClient,
    *,
    identity_id: int,
    env: str,
) -> bool:
    try:
        status_map = await bridge.batch_creator_brief_status([identity_id], env=env)
    except BridgeError:
        return False
    readiness = status_map.get(identity_id) or {}
    return bool(readiness.get("ready"))


async def _launch_auto_outreach_draft(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    identity_id: int,
    trigger_run_id: str,
    audit_action: str,
    audit_extra: dict[str, Any] | None = None,
    log_prefix: str = "post_enrichment_auto_draft",
) -> dict[str, Any] | None:
    """Shared redraft launch after email discover or creator-brief refresh."""
    dedup_key = f"redraft:{env}:{campaign_id}:{identity_id}"
    if get_inflight_run(conn, dedup_key=dedup_key) is not None:
        log.info(
            "%s: redraft inflight for %s/%s/%s",
            log_prefix, campaign_id, env, identity_id,
        )
        return None

    if not await _is_approved_candidate(
        bridge,
        campaign_id=campaign_id,
        identity_id=identity_id,
        env=env,
    ):
        return None

    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        log.warning(
            "%s: get_identity failed for %s: %s",
            log_prefix, identity_id, exc,
        )
        return None
    if not ident:
        return None

    email = str(ident.get("primary_email") or "").strip()
    if not email:
        log.info(
            "%s: trigger %s completed but identity %s has no email — skip",
            log_prefix, trigger_run_id, identity_id,
        )
        return None

    try:
        facts_resp = await bridge.read_facts(
            identity_id, campaign_id=campaign_id, env=env,
        )
    except BridgeError as exc:
        log.warning(
            "%s: read_facts failed for %s: %s",
            log_prefix, identity_id, exc,
        )
        return None
    facts = facts_resp.get("facts") if isinstance(facts_resp, dict) else {}
    facts = facts if isinstance(facts, dict) else {}

    if facts.get("offer.outreach_sent"):
        return None
    if _draft_already_exists(facts):
        return None

    if not await _creator_brief_ready(bridge, identity_id=identity_id, env=env):
        log.info(
            "%s: creator brief not ready for %s/%s — defer auto-draft",
            log_prefix, campaign_id, identity_id,
        )
        return None

    if not await _campaign_config_ready(bridge, campaign_id):
        log.warning(
            "%s: campaign_config incomplete for %s",
            log_prefix, campaign_id,
        )
        return None

    if not _ensure_bridge_key_for_gateway():
        return None

    campaign_row = conn.execute(
        "SELECT test_mode_to FROM product_campaigns WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    test_mode_to = campaign_row["test_mode_to"] if campaign_row else None
    if env == "TEST" and not test_mode_to:
        log.warning(
            "%s: test_mode_to missing for %s/%s",
            log_prefix, campaign_id, env,
        )
        return None

    actor_email, actor_user_id = _resolve_approve_actor(
        conn, campaign_id=campaign_id, env=env,
    )
    handle = ident.get("primary_handle")
    handle_str = handle if isinstance(handle, str) else None

    try:
        campaign_snapshot = await bridge.get_campaign(campaign_id, env=env)
    except BridgeError as exc:
        log.warning(
            "%s: get_campaign failed for %s: %s",
            log_prefix, campaign_id, exc,
        )
        return None
    try:
        dispatch_raw = await bridge.get_dispatch_context(
            identity_id, campaign_id, env=env,
        )
    except BridgeError as exc:
        log.warning(
            "%s: get_dispatch_context failed for %s: %s",
            log_prefix, identity_id, exc,
        )
        return None
    dispatch_snapshot = (
        slim_dispatch_context_for_agent(dispatch_raw)
        if isinstance(dispatch_raw, dict)
        else dispatch_raw
    )

    from .routers.campaigns import (  # noqa: PLC0415 — avoid import cycle at load
        _REDraft_OUTREACH_INSTRUCTIONS,
        _compose_redraft_brief,
    )

    brief = _compose_redraft_brief(
        campaign_id=campaign_id,
        env=env,
        identity_id=identity_id,
        handle=handle_str,
        actor_email=actor_email,
        actor_user_id=actor_user_id,
        test_mode_to=test_mode_to,
        campaign_snapshot=(
            campaign_snapshot if isinstance(campaign_snapshot, dict) else {}
        ),
        identity_snapshot=ident if isinstance(ident, dict) else {},
        dispatch_context_snapshot=(
            dispatch_snapshot if isinstance(dispatch_snapshot, dict) else {}
        ),
    )
    draft_session_id = campaign_draft_session_id(env, campaign_id, identity_id)

    async def _start_redraft() -> dict[str, Any]:
        return await gateway.start_run_with_retry(
            input=brief,
            instructions=_REDraft_OUTREACH_INSTRUCTIONS,
            session_id=draft_session_id,
        )

    try:
        run = await gateway.launch_via_queue(
            _start_redraft,
            session_id=draft_session_id,
            dedup_key=dedup_key,
        )
    except GatewayError as exc:
        log.warning(
            "%s: gateway launch failed for %s/%s: %s",
            log_prefix, campaign_id, identity_id, exc,
        )
        return None

    new_run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(new_run_id, str) and new_run_id:
        gateway.ensure_run_drained(new_run_id)
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=new_run_id,
            kind="draft",
            session_id=draft_session_id,
            dedup_key=dedup_key,
        )
    payload: dict[str, Any] = {
        "identity_id": identity_id,
        "env": env,
        "trigger_run_id": trigger_run_id,
        "draft_run_id": new_run_id,
        "email": email,
    }
    if audit_extra:
        payload.update(audit_extra)
    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action=audit_action,
        target=campaign_id,
        payload=payload,
    )
    log.info(
        "%s: started draft run %s for identity %s after trigger %s",
        log_prefix, new_run_id, identity_id, trigger_run_id,
    )
    return {
        "identity_id": identity_id,
        "draft_run_id": new_run_id,
        "trigger_run_id": trigger_run_id,
        "status": "started",
    }


async def maybe_trigger_outreach_draft_after_email_discover(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    session_id: str,
    discover_run_id: str,
) -> dict[str, Any] | None:
    """Launch outreach draft when email discovery completes with email + brief."""
    parsed = parse_email_discover_session(session_id)
    if parsed is None:
        return None
    parsed_env, identity_id = parsed
    if parsed_env != env:
        return None

    if not await _is_approved_candidate(
        bridge,
        campaign_id=campaign_id,
        identity_id=identity_id,
        env=env,
    ):
        return None

    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError:
        return None
    if not ident:
        return None

    email = str(ident.get("primary_email") or "").strip()
    if not email:
        log.info(
            "post_email_discover_draft: discover run %s completed but "
            "identity %s still has no email — skip auto-draft",
            discover_run_id, identity_id,
        )
        return None

    try:
        facts_resp = await bridge.read_facts(
            identity_id, campaign_id=campaign_id, env=env,
        )
    except BridgeError:
        return None
    facts = facts_resp.get("facts") if isinstance(facts_resp, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    if facts.get("offer.outreach_sent") or _draft_already_exists(facts):
        return None

    if not await _creator_brief_ready(bridge, identity_id=identity_id, env=env):
        log.info(
            "post_email_discover_draft: brief not ready for %s/%s after "
            "discover %s — queue kol-creator-brief-refresh",
            campaign_id, identity_id, discover_run_id,
        )
        from .creator_brief_dispatch import (  # noqa: PLC0415
            dispatch_creator_brief_refresh_for_identity,
        )

        actor_email, actor_user_id = _resolve_approve_actor(
            conn, campaign_id=campaign_id, env=env,
        )
        queued = await dispatch_creator_brief_refresh_for_identity(
            bridge=bridge,
            gateway=gateway,
            conn=conn,
            identity_id=identity_id,
            env=env,
            campaign_id=campaign_id,
            actor_email=actor_email,
            actor_user_id=actor_user_id or 0,
            audit_action="kol.creator_brief.refresh_after_email_discover",
            source="post_email_discover",
        )
        return {
            "identity_id": identity_id,
            "status": "deferred_creator_brief",
            "discover_run_id": discover_run_id,
            "brief_refresh": queued,
        }

    return await _launch_auto_outreach_draft(
        bridge=bridge,
        gateway=gateway,
        conn=conn,
        campaign_id=campaign_id,
        env=env,
        identity_id=identity_id,
        trigger_run_id=discover_run_id,
        audit_action="campaign.auto_draft_after_email_discover",
        audit_extra={"discover_run_id": discover_run_id},
        log_prefix="post_email_discover_draft",
    )


async def maybe_trigger_outreach_draft_after_creator_brief_refresh(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    session_id: str,
    brief_refresh_run_id: str,
) -> dict[str, Any] | None:
    """Launch outreach draft when approve-time brief refresh completes."""
    parsed = parse_creator_brief_refresh_session(session_id)
    if parsed is None:
        return None
    parsed_env, identity_id = parsed
    if parsed_env != env:
        return None

    return await _launch_auto_outreach_draft(
        bridge=bridge,
        gateway=gateway,
        conn=conn,
        campaign_id=campaign_id,
        env=env,
        identity_id=identity_id,
        trigger_run_id=brief_refresh_run_id,
        audit_action="campaign.auto_draft_after_creator_brief_refresh",
        audit_extra={"brief_refresh_run_id": brief_refresh_run_id},
        log_prefix="post_creator_brief_refresh_draft",
    )
