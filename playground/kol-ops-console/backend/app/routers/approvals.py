"""Proxy routes for cross-cutting approvals list (Phase C-i).

The bridge's ``GET /approvals`` returns rows shaped as
``{identity_id, campaign_id, fact_key, value, captured_at}`` (see
``cal.list_pending_approvals``). The frontend ``ApprovalsPage``, however,
consumes ``ApprovalRow`` with ``fact_path / namespace / context /
opened_by / opened_at / linked_escalation_id / handle``. This router
normalizes the bridge shape into the frontend contract so the approve /
reject buttons resolve to a real ``approval.*`` ``fact_path`` and the
list re-renders correctly after a decision is written.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

_REPO_ROOT = str(Path(__file__).resolve().parents[5])

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..bridge_runtime import ensure_gateway_bridge_key
from ..campaign_config_sync import assert_campaign_config_complete
from ..campaign_id_norm import CampaignIdNormaliserMixin
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError
from ..run_registry import get_inflight_run, register_run


def _refine_dedup_key(identity_id: int, campaign_id: str) -> str:
    """In-flight key for refine of approval.reply_draft. One operator can
    only have one refine in flight per (identity, campaign) tuple; two
    parallel refines would race the last-writer-wins fact update."""
    return f"refine:{identity_id}:{campaign_id}"

router = APIRouter(prefix="/approvals", tags=["approvals"])

# Approvals whose decision is itself the deliverable. After approve, no
# further agent run is needed (the bridge produced the artefact inline,
# e.g. a Gmail draft for ``approval.reply_draft``).
_TERMINAL_APPROVAL_FACT_PATHS: frozenset[str] = frozenset({"approval.reply_draft"})

_APPROVAL_RESUME_INSTRUCTIONS = (
    "You are resuming a KOL outreach campaign after a web-console approval "
    "decision.\n"
    "Read the campaign, candidate, identity, goal and event state from CAL "
    "via the deterministic kol_bridge_tool.py CLI, always passing the env "
    "from the brief. Do not rerun unrelated discovery.\n"
    "The brief below tells you which approval.* fact was decided. Read the "
    "latest value of that fact through the bridge CLI to recover both the "
    "operator decision and the original proposal payload (which is now "
    "preserved under the `value` key alongside `decision`). Continue the "
    "blocked step that produced the approval request:\n"
    "- If decision=approved, proceed with the approved action.\n"
    "- If decision=rejected, do not retry the rejected action; the bridge "
    "has opened a derived escalation (see derived_escalation_id) and you "
    "should wait for the operator to answer it.\n"
    "In TEST mode, route any draft or Gmail test target to "
    "campaign_config.test_mode_to. Never send email without a separate "
    "explicit operator approval. Persist progress, draft records, "
    "approvals, or any new escalation through the bridge CLI."
)


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class DecisionBody(CampaignIdNormaliserMixin):
    identity_id: int
    campaign_id: Optional[str] = None
    decided_by: str = Field(min_length=1, max_length=120)
    note: Optional[str] = Field(default=None, max_length=1000)
    env: Optional[str] = None


class RefineBody(CampaignIdNormaliserMixin):
    """Body for POST /approvals/{fact_path}/refine.

    `campaign_id` is required: regeneration starts a campaign-scoped
    gateway run so the agent can re-read dispatch context and the
    inbound message. Sentinel strings (``"null"``, ``"undefined"``)
    are normalised to ``None`` by the mixin first, which then trips
    the ``min_length=1`` constraint for a clean 422.

    ``if_captured_at`` is an optional optimistic-lock token — the
    ``captured_at`` value the operator saw on the row they're refining.
    If the bridge's current row has a newer ``captured_at`` (another
    refine landed first, or the row was approved/rejected), the server
    refuses with 409 to prevent clobbering a fresher result.
    """
    identity_id: int
    campaign_id: str = Field(min_length=1)
    refinement_prompt: str = Field(min_length=1, max_length=4000)
    env: Optional[str] = Field(default=None, pattern="^(LIVE|TEST)$")
    if_captured_at: Optional[str] = None


_REFINE_DRAFT_INSTRUCTIONS = (
    "You are REFINING an existing pending approval.reply_draft based on an "
    "operator's natural-language guidance. Hard rules:\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- Read the current fact value via\n"
    "  `kol_bridge_tool.py get-facts --identity-id <id> --campaign-id <cid> "
    "  --env <env>` and pull out the `approval.reply_draft` entry.\n"
    "- The fact carries `child_skill`, `source_message_id`, `primary_lane`,\n"
    "  `primary_goal`, and the prior `draft` envelope. The operator's\n"
    "  refinement is in the brief under `operator_refinement_prompt`.\n"
    "- Re-invoke the SAME `child_skill` named in the fact with the original\n"
    "  pending-reply payload (recover from kol_inbound_reply events for\n"
    "  source_message_id) PLUS the operator_refinement_prompt as an extra\n"
    "  input field. The skill must treat the prompt as a hard constraint on\n"
    "  draft content (tone, additions, removals). Do NOT rewrite offer.*\n"
    "  or other domain facts; a refinement run is content-only.\n"
    "- Do NOT open a new escalation, do NOT change `decision` (must remain\n"
    "  \"pending\"), do NOT send mail, do NOT create a Gmail draft.\n"
    "- Persist the result by writing back the SAME approval.reply_draft fact\n"
    "  via `kol_bridge_tool.py write-facts-multi`. The new value MUST:\n"
    "    * preserve dollar amounts exactly. Never place JSON containing `$`\n"
    "      amounts in an unquoted heredoc or inline double-quoted shell\n"
    "      string; bash expands `$3000` to `000` and `$800` to `00`. Write\n"
    "      JSON with `cat <<'JSON' > /tmp/draft.json` or Python\n"
    "      `json.dump`, then pass `--json @/tmp/draft.json`;\n"
    "    * keep `decision`, `source_message_id`, `primary_lane`,\n"
    "      `primary_goal`, `child_skill`, `linked_escalation_id` from the\n"
    "      prior value;\n"
    "    * set `draft` to the new envelope returned by the child skill;\n"
    "    * prepend the OLD draft into `previous_drafts` (cap the array at\n"
    "      the 5 most recent entries);\n"
    "    * append `{prompt, at, by}` to `refinement_history` (cap at 5),\n"
    "      where `by` is the requested_by from the brief and `at` is the\n"
    "      current ISO-8601 UTC timestamp.\n"
    "- After writing, re-read `approval.reply_draft` and verify that money\n"
    "  strings did not become placeholders like `000 quote` or `00 total`.\n"
    "- In TEST mode, route any draft target to campaign_config.test_mode_to."
)


def _compose_refine_brief(
    *,
    fact_path: str,
    identity_id: int,
    campaign_id: str,
    env: str,
    current_value: dict[str, Any],
    refinement_prompt: str,
    actor_email: str,
) -> str:
    return "\n".join([
        "# approval_refine",
        f"fact_path: {fact_path}",
        f"identity_id: {identity_id}",
        f"campaign_id: {campaign_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
        "",
        "# current_value_json",
        json.dumps(current_value, ensure_ascii=False, sort_keys=True, default=str),
        "",
        "# operator_refinement_prompt",
        refinement_prompt.strip(),
        "",
        "# required_output",
        ("Re-invoke the child_skill named in current_value_json with the "
         "original inbound context PLUS operator_refinement_prompt, then "
         "write back the same approval.reply_draft fact with the new draft "
         "envelope, the prior envelope moved into previous_drafts (cap 5), "
         "and a new refinement_history entry (cap 5). Keep decision=pending. "
         "Do NOT send mail. Do NOT create a Gmail draft. Report the new "
         "fact_path back when done so the console can poll for it."),
    ])


def _to_row(raw: dict[str, Any], handle_map: dict[int, str | None]) -> dict[str, Any]:
    """Normalize one bridge approval row into the frontend ``ApprovalRow``."""
    fact_key = raw.get("fact_key") or raw.get("fact_path") or ""
    namespace = fact_key.split(".", 1)[0] if fact_key else ""
    value = raw.get("value")
    if isinstance(value, dict):
        context: dict[str, Any] | None = value
        opened_by = value.get("opened_by") or value.get("source")
        linked_escalation_id = value.get("linked_escalation_id") or value.get("escalation_id")
    elif value is None:
        context = None
        opened_by = None
        linked_escalation_id = None
    else:
        context = {"value": value}
        opened_by = None
        linked_escalation_id = None
    identity_id = raw.get("identity_id")
    return {
        "identity_id": identity_id,
        "campaign_id": raw.get("campaign_id"),
        "fact_path": fact_key,
        "namespace": namespace,
        "context": context,
        "opened_by": opened_by,
        "opened_at": raw.get("captured_at"),
        "linked_escalation_id": linked_escalation_id,
        "handle": handle_map.get(identity_id) if isinstance(identity_id, int) else None,
    }


async def _fetch_handles(
    bridge: BridgeClient, identity_ids: list[int]
) -> dict[int, str | None]:
    if not identity_ids:
        return {}

    async def _one(iid: int) -> tuple[int, str | None]:
        try:
            ident = await bridge.get_identity(iid)
        except BridgeError:
            return iid, None
        if not isinstance(ident, dict):
            return iid, None
        handle = ident.get("primary_handle") or ident.get("handle")
        return iid, str(handle).lstrip("@") if handle else None

    pairs = await asyncio.gather(*(_one(i) for i in identity_ids))
    return {iid: handle for iid, handle in pairs}


def _shape_inbound(ev: dict[str, Any]) -> dict[str, Any]:
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    return {
        "event_id": ev.get("id"),
        "ts": ev.get("ts"),
        "from_addr": payload.get("from_addr"),
        "subject": payload.get("subject"),
        "body": payload.get("body"),
        "snippet": payload.get("snippet"),
        "date": payload.get("date"),
        "message_id": payload.get("message_id"),
        "thread_id": payload.get("thread_id"),
    }


@router.get("/inbound-context")
async def approval_inbound_context(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    identity_id: int = Query(..., ge=1),
    campaign_id: str = Query(...),
    message_id: Optional[str] = Query(None),
    env: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Return the inbound email tied to a pending approval.reply_draft.

    The approval value usually contains a ``source_message_id`` we can
    match against; when absent (older drafts), fall back to the latest
    inbound on the per-campaign timeline.
    """
    e = _env(env)
    try:
        events = await bridge.get_timeline(
            identity_id, env=e, campaign_id=campaign_id, limit=200,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    inbounds = [
        ev for ev in events
        if isinstance(ev, dict) and ev.get("event_type") == "kol_inbound_reply"
    ]
    chosen: dict[str, Any] | None = None
    if message_id:
        for ev in inbounds:
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            if payload.get("message_id") == message_id:
                chosen = ev
                break
    if chosen is None and inbounds:
        chosen = inbounds[0]
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": e,
        "inbound": _shape_inbound(chosen) if chosen else None,
    }


@router.get("")
async def list_approvals(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    status_filter: str = Query("pending", alias="status"),
    env: Optional[str] = Query(None),
) -> list[dict[str, Any]]:
    if status_filter not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown status: {status_filter}")
    try:
        raw = await bridge.list_approvals(status=status_filter, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    unique_ids = sorted({
        r["identity_id"] for r in raw
        if isinstance(r, dict) and isinstance(r.get("identity_id"), int)
    })
    handle_map = await _fetch_handles(bridge, unique_ids)
    return [_to_row(r, handle_map) for r in raw if isinstance(r, dict)]


def _compose_approval_resume_brief(
    *,
    fact_path: str,
    decision: str,
    identity_id: int,
    campaign_id: str,
    env: str,
    decided_by: str,
    note: Optional[str],
    bridge_response: dict[str, Any],
) -> str:
    """Compose the brief handed to the gateway when resuming after an
    approval decision. Mirrors ``_compose_resume_brief`` in
    ``routers/escalations.py``.
    """
    approved_value = bridge_response.get("value")
    derived_escalation_id = bridge_response.get("derived_escalation_id")
    return "\n".join([
        "# approval_resume",
        f"fact_path: {fact_path}",
        f"decision: {decision}",
        f"campaign_id: {campaign_id}",
        f"identity_id: {identity_id}",
        f"mode: {env}",
        f"decided_by: {decided_by}",
        f"note: {note or ''}",
        "",
        "# approved_value_json",
        json.dumps(approved_value, ensure_ascii=False, sort_keys=True, default=str),
        "",
        "# derived_escalation_id",
        str(derived_escalation_id) if derived_escalation_id is not None else "",
        "",
        "# required_next_step",
        "Continue the blocked step that produced this approval request. "
        "If the approved value is ambiguous or insufficient to proceed "
        "safely, open a new specific escalation instead of inventing data.",
    ])


async def _start_approval_resume_run(
    *,
    gateway: GatewayClient,
    conn,
    fact_path: str,
    decision: str,
    body: DecisionBody,
    env: str,
    decided_by: str,
    bridge_response: dict[str, Any],
) -> Optional[str]:
    """Start a gateway run to continue the campaign after a decision.

    Returns the new ``run_id`` (and updates ``product_campaigns``) when a
    run is dispatched. Returns ``None`` for terminal fact_paths and for
    rejects (where the bridge has already opened a derived escalation —
    the resume will be driven by that escalation's resolve instead).
    """
    if decision != "approved":
        return None
    if fact_path in _TERMINAL_APPROVAL_FACT_PATHS:
        return None
    if not body.campaign_id:
        # Approval not tied to a campaign — nothing to resume. The bridge
        # has already persisted the decision; this just skips the gateway
        # run path, which requires campaign_id for session/registry keys.
        return None
    ensure_gateway_bridge_key()
    brief = _compose_approval_resume_brief(
        fact_path=fact_path,
        decision=decision,
        identity_id=body.identity_id,
        campaign_id=body.campaign_id,
        env=env,
        decided_by=decided_by,
        note=body.note,
        bridge_response=bridge_response,
    )
    try:
        run = await gateway.start_run(
            input=brief,
            instructions=_APPROVAL_RESUME_INSTRUCTIONS,
            session_id=f"kol-campaign:{env}:{body.campaign_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str):
        conn.execute(
            "UPDATE product_campaigns SET run_id=?, status='running' "
            "WHERE campaign_id=? AND env=?",
            (run_id, body.campaign_id, env),
        )
        register_run(
            conn,
            campaign_id=body.campaign_id,
            env=env,
            run_id=run_id,
            kind="resume",
            session_id=f"kol-campaign:{env}:{body.campaign_id}",
        )
        return run_id
    return None


@router.post("/{fact_path:path}/approve")
async def approve(
    fact_path: str,
    body: DecisionBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    env = _env(payload.get("env"))
    payload["env"] = env
    if body.campaign_id and fact_path not in _TERMINAL_APPROVAL_FACT_PATHS:
        ensure_gateway_bridge_key()
    try:
        out = await bridge.approve(fact_path, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = await _start_approval_resume_run(
        gateway=gateway,
        conn=conn,
        fact_path=fact_path,
        decision="approved",
        body=body,
        env=env,
        decided_by=f"web:{user['email']}",
        bridge_response=out if isinstance(out, dict) else {},
    )
    write_audit(
        conn, actor_user_id=user["id"], action="approval.approve",
        target=fact_path,
        payload={
            "identity_id": body.identity_id,
            "campaign_id": body.campaign_id,
            "run_id": run_id,
        },
    )
    return {**(out if isinstance(out, dict) else {}), "run_id": run_id}


@router.post("/{fact_path:path}/reject")
async def reject(
    fact_path: str,
    body: DecisionBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.reject(fact_path, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    # No gateway resume on reject: the bridge opens a derived escalation
    # (see plugin_api._approve_or_reject). The agent will be resumed when
    # the operator resolves that escalation via EscalationConsolePage.
    derived_escalation_id = (
        out.get("derived_escalation_id") if isinstance(out, dict) else None
    )
    write_audit(
        conn, actor_user_id=user["id"], action="approval.reject",
        target=fact_path,
        payload={
            "identity_id": body.identity_id,
            "campaign_id": body.campaign_id,
            "note": body.note,
            "derived_escalation_id": derived_escalation_id,
        },
    )
    return out


@router.post("/{fact_path:path}/refine")
async def refine(
    fact_path: str,
    body: RefineBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    """Kick off a content-only regeneration of a pending approval.reply_draft.

    The agent re-invokes the original child_skill with the same inbound
    context plus the operator's natural-language refinement prompt, then
    rewrites the same approval.reply_draft fact (keeping decision=pending,
    moving the prior draft into previous_drafts, and recording the prompt
    in refinement_history). Async — the row re-renders on the next poll.
    """
    if fact_path != "approval.reply_draft":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "refine is only supported for approval.reply_draft",
        )
    env = _env(body.env)
    ensure_gateway_bridge_key()
    # In-flight dedup: a previous refine for the same (identity, campaign)
    # may still be writing back the fact. Block the duplicate at this
    # layer so the frontend can keep the button disabled across page
    # refreshes — the local React busy state alone resets on reload.
    dedup_key = _refine_dedup_key(body.identity_id, body.campaign_id)
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "refine_already_in_flight",
                "message": (
                    "A refine run for this approval is already in "
                    "progress. Wait for the new draft to appear "
                    "(typically 30–60 s) before requesting another."
                ),
                "run_id": inflight.get("run_id"),
                "started_at": inflight.get("started_at"),
            },
        )
    # Refine uses a fresh session_id (no transcript replay from launch),
    # so the child skill must read product_display_name from CAL. Block
    # upfront if it's missing — every refine path (cold/reengagement/
    # contract/followup) writes operator-facing copy that references the
    # product and is constrained by the SKU-leak guard.
    await assert_campaign_config_complete(bridge, body.campaign_id)
    try:
        raw = await bridge.list_approvals(status="pending", env=env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    current_row: dict[str, Any] | None = None
    for r in raw:
        if not isinstance(r, dict):
            continue
        if (r.get("identity_id") == body.identity_id
                and r.get("campaign_id") == body.campaign_id
                and (r.get("fact_key") or r.get("fact_path")) == fact_path):
            current_row = r
            break
    if current_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no pending approval.reply_draft for that identity/campaign",
        )
    current_value = current_row.get("value")
    if not isinstance(current_value, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "approval.reply_draft value is not an object — cannot refine",
        )
    # Optimistic lock: refuse if the row moved since the operator opened
    # the refine UI (a concurrent refine, an approve, or a reject all
    # change captured_at). Best-effort — only enforced when the client
    # sent a token.
    if body.if_captured_at is not None:
        current_captured_at = current_row.get("captured_at")
        if (current_captured_at is not None
                and str(current_captured_at) != str(body.if_captured_at)):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "error": "stale_draft",
                    "message": (
                        "This draft has been updated by another action "
                        "since you opened it. Refresh the Approvals "
                        "page to see the latest version before "
                        "refining."
                    ),
                    "current_captured_at": current_captured_at,
                    "expected_captured_at": body.if_captured_at,
                },
            )
    brief = _compose_refine_brief(
        fact_path=fact_path,
        identity_id=body.identity_id,
        campaign_id=body.campaign_id,
        env=env,
        current_value=current_value,
        refinement_prompt=body.refinement_prompt,
        actor_email=user["email"],
    )
    try:
        run = await gateway.start_run(
            input=brief,
            instructions=_REFINE_DRAFT_INSTRUCTIONS,
            # Same session-id namespace as preview_draft so replay logic
            # treats this as a draft run, not a campaign resume.
            session_id=f"kol-campaign-draft:{env}:{body.campaign_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id:
        register_run(
            conn,
            campaign_id=body.campaign_id,
            env=env,
            run_id=run_id,
            kind="refine",
            session_id=f"kol-campaign-draft:{env}:{body.campaign_id}",
            dedup_key=dedup_key,
        )
    write_audit(
        conn, actor_user_id=user["id"], action="approval.refine",
        target=fact_path,
        payload={
            "identity_id": body.identity_id,
            "campaign_id": body.campaign_id,
            "run_id": run_id,
            "refinement_prompt": body.refinement_prompt,
        },
    )
    return {
        "ok": True,
        "run_id": run_id,
        "hint": ("agent is regenerating the draft asynchronously; "
                 "the row will refresh with the new draft in 30–60s."),
    }
