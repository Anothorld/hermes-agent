"""Auto-trigger initial outreach draft after approve-time email discovery."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import sqlite3

from .audit import write_audit
from .bridge_client import BridgeClient, BridgeError
from .bridge_runtime import BRIDGE_KEY_ENV, resolve_bridge_key
from .campaign_config_sync import DEFAULT_REQUIRED_FIELDS
from .gateway_client import GatewayClient, GatewayError
from .run_registry import get_inflight_run, register_run
from .session_ids import campaign_draft_session_id

log = logging.getLogger(__name__)

_SYSTEM_ACTOR_EMAIL = "system:post_email_discover"


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
        log.warning("post_email_discover_draft: bridge key missing; skip auto-draft")
        return False
    os.environ[BRIDGE_KEY_ENV] = key
    os.environ.setdefault("KOC_BRIDGE_KEY", key)
    return True


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
    """Launch a single-identity outreach draft when email discovery succeeds.

    Called from the run-state reconciler when a ``kol-email-discover:*`` run
    reaches ``completed``. Returns a status dict when a draft run is started,
    or ``None`` when the follow-up is not applicable.
    """
    parsed = parse_email_discover_session(session_id)
    if parsed is None:
        return None
    parsed_env, identity_id = parsed
    if parsed_env != env:
        return None

    dedup_key = f"redraft:{env}:{campaign_id}:{identity_id}"
    if get_inflight_run(conn, dedup_key=dedup_key) is not None:
        log.info(
            "post_email_discover_draft: redraft inflight for %s/%s/%s",
            campaign_id, env, identity_id,
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
            "post_email_discover_draft: get_identity failed for %s: %s",
            identity_id, exc,
        )
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
    except BridgeError as exc:
        log.warning(
            "post_email_discover_draft: read_facts failed for %s: %s",
            identity_id, exc,
        )
        return None
    facts = facts_resp.get("facts") if isinstance(facts_resp, dict) else {}
    facts = facts if isinstance(facts, dict) else {}

    if facts.get("offer.outreach_sent"):
        return None
    if _draft_already_exists(facts):
        return None

    if not await _campaign_config_ready(bridge, campaign_id):
        log.warning(
            "post_email_discover_draft: campaign_config incomplete for %s",
            campaign_id,
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
            "post_email_discover_draft: test_mode_to missing for %s/%s",
            campaign_id, env,
        )
        return None

    actor_email, actor_user_id = _resolve_approve_actor(
        conn, campaign_id=campaign_id, env=env,
    )
    handle = ident.get("primary_handle")
    handle_str = handle if isinstance(handle, str) else None

    from .routers.campaigns import (  # noqa: PLC0415 — avoid import cycle at load
        _APPROVAL_INSTRUCTIONS,
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
    )
    draft_session_id = campaign_draft_session_id(env, campaign_id, identity_id)

    async def _start_redraft() -> dict[str, Any]:
        return await gateway.start_run_with_retry(
            input=brief,
            instructions=_APPROVAL_INSTRUCTIONS,
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
            "post_email_discover_draft: gateway launch failed for %s/%s: %s",
            campaign_id, identity_id, exc,
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
    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action="campaign.auto_draft_after_email_discover",
        target=campaign_id,
        payload={
            "identity_id": identity_id,
            "env": env,
            "discover_run_id": discover_run_id,
            "draft_run_id": new_run_id,
            "email": email,
        },
    )
    log.info(
        "post_email_discover_draft: started draft run %s for identity %s "
        "after discover run %s",
        new_run_id, identity_id, discover_run_id,
    )
    return {
        "identity_id": identity_id,
        "draft_run_id": new_run_id,
        "discover_run_id": discover_run_id,
        "status": "started",
    }
