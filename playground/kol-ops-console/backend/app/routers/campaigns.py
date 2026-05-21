"""Start campaigns (proxy to bridge ``/campaigns/{id}/start``)."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _compose_brief(campaign_id: str, product: sqlite3.Row, body: "StartCampaignBody") -> str:
    tags = json.loads(product["tags_json"] or "[]")
    sku_ref = product["url"] or product["sku"]
    lines = [
        "Start a KOL outreach campaign via the web console.",
        f"campaign_id: {campaign_id}",
        f"product_sku: {product['sku']}",
        f"product_name: {product['name']}",
        f"mode: {body.env}",
        "sku_whitelist:",
        f"  - {sku_ref}",
        f"budget_total: {body.budget_total:g}",
        f"budget_per_kol: {body.budget_per_kol:g}",
        f"absolute_floor: {body.absolute_floor:g}",
        f"headcount_target: {body.headcount_target}",
        f"test_mode_to: {body.test_mode_to}",
        "triggered_by: web",
    ]
    if product["url"]:
        lines.append(f"product_url: {product['url']}")
    if tags:
        lines.append(f"product_tags: {', '.join(tags)}")
    if product["notes"]:
        lines.extend(["product_notes:", product["notes"]])
    extra = (body.brief_extra or "").strip()
    if extra:
        lines.extend([
            "",
            "# operator_brief (supplied via web console)",
            extra,
        ])
    return "\n".join(lines)


# Cap operator-supplied brief to keep upstream token cost predictable.
# 16k chars ~ 4k tokens, plenty for a product one-pager.
_MAX_BRIEF_EXTRA = 16_000


class StartCampaignBody(BaseModel):
    product_sku: str
    budget_per_kol: float = Field(gt=0)
    absolute_floor: float = Field(gt=0)
    budget_total: float = Field(gt=0)
    headcount_target: int = Field(ge=1, le=200)
    test_mode_to: str
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    brief_extra: str | None = Field(
        default=None,
        max_length=_MAX_BRIEF_EXTRA,
        description="Optional operator-supplied product brief (markdown or plain text).",
    )


@router.post("/{campaign_id}/start")
async def start(
    campaign_id: str,
    body: StartCampaignBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    force: bool = Query(False, description="Override duplicate-campaign guard."),
) -> dict:
    product = conn.execute(
        "SELECT sku, name, url, tags_json, notes FROM products WHERE sku=?",
        (body.product_sku,),
    ).fetchone()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sku not found")

    # Anti-duplicate guard. The bridge does not currently dedupe, so the
    # console owns this check. ``force=true`` lets the operator re-fire
    # intentionally (e.g. after a 402 failure) without dropping the audit row.
    if not force:
        existing = conn.execute(
            "SELECT run_id, status FROM product_campaigns WHERE campaign_id=? AND env=?",
            (campaign_id, body.env),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"campaign already started (run_id={existing['run_id']}, "
                f"status={existing['status']}); pass ?force=true to retry",
            )
        active = conn.execute(
            "SELECT campaign_id, run_id FROM product_campaigns "
            "WHERE sku=? AND env=? AND status='running' LIMIT 1",
            (product["sku"], body.env),
        ).fetchone()
        if active is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"sku already has a running campaign "
                f"(campaign_id={active['campaign_id']}, run_id={active['run_id']}); "
                "close it first or pass ?force=true",
            )

    payload = body.model_dump()
    sku_ref = product["url"] or product["sku"]
    payload["product_name"] = product["name"]
    payload["product_url"] = product["url"]
    payload["sku_whitelist"] = [sku_ref]
    payload["brief"] = _compose_brief(campaign_id, product, body)
    payload["triggered_by"] = "web"
    payload["actor"] = f"web:{user['email']}"
    try:
        out = await bridge.start_campaign(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    run_id = out.get("run_id") if isinstance(out, dict) else None
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO product_campaigns
             (sku, campaign_id, env, run_id, started_at, started_by_user_id, status)
           VALUES (?,?,?,?,?,?, 'running')
           ON CONFLICT(campaign_id, env) DO UPDATE SET
             run_id=excluded.run_id,
             started_at=excluded.started_at,
             started_by_user_id=excluded.started_by_user_id,
             status='running'""",
        (product["sku"], campaign_id, body.env, run_id, now, user["id"]),
    )
    write_audit(conn, actor_user_id=user["id"], action="campaign.start",
                target=campaign_id, payload=payload)
    return out


class CloseCampaignBody(BaseModel):
    status: str = Field(default="closed", pattern="^(closed|cancelled)$")


@router.post("/{campaign_id}/close")
def close(
    campaign_id: str,
    body: CloseCampaignBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    env: str = Query(..., pattern="^(LIVE|TEST)$"),
) -> dict:
    """Mark a Web-tracked campaign as no longer running.

    Used to clear the duplicate-trigger guard once the operator has handled
    the run (e.g. cancelled after an upstream 402, or shipped end-to-end).
    """
    row = conn.execute(
        "SELECT status FROM product_campaigns WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not tracked")
    conn.execute(
        "UPDATE product_campaigns SET status=? WHERE campaign_id=? AND env=?",
        (body.status, campaign_id, env),
    )
    write_audit(conn, actor_user_id=user["id"], action="campaign.close",
                target=campaign_id, payload={"env": env, "status": body.status})
    return {"ok": True, "campaign_id": campaign_id, "env": env, "status": body.status}


class ApproveShortlistBody(BaseModel):
    """Body for the operator's shortlist approval click."""

    selected_handles: list[str] = Field(default_factory=list)
    note: str | None = None
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


@router.get("/{campaign_id}/shortlist")
async def get_shortlist(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
) -> dict:
    """Return the agent's latest shortlist_ready payload (candidates + scores).

    Used by the per-product review panel so operators can pick a subset
    before approval.
    """
    try:
        return await bridge.get_shortlist(campaign_id, env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{campaign_id}/approve-shortlist")
async def approve_shortlist(
    campaign_id: str,
    body: ApproveShortlistBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Forward shortlist approval to the bridge; record audit row."""
    payload = body.model_dump()
    payload["actor"] = f"web:{user['email']}"
    payload["triggered_by"] = "web"
    try:
        out = await bridge.approve_shortlist(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    new_run_id = out.get("run_id") if isinstance(out, dict) else None
    if new_run_id:
        conn.execute(
            "UPDATE product_campaigns SET run_id=?, status='running' "
            "WHERE campaign_id=? AND env=?",
            (new_run_id, campaign_id, body.env),
        )
    write_audit(conn, actor_user_id=user["id"], action="campaign.approve_shortlist",
                target=campaign_id, payload=payload)
    return out


class InboundReplyBody(BaseModel):
    """Body for the operator's reply-simulation click."""

    kol_identity_id: int = Field(gt=0)
    body: str = Field(min_length=1, max_length=16_000)
    intent_hint: str | None = Field(
        default=None,
        pattern="^(interested|asking_fee|decline|out_of_office|spam|unknown)$",
    )
    from_addr: str | None = None
    product_sku: str | None = None
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


@router.post("/{campaign_id}/replies/inbound")
async def inject_reply(
    campaign_id: str,
    body: InboundReplyBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Forward a simulated inbound reply to the bridge for processing."""
    payload = body.model_dump()
    payload["actor"] = f"web:{user['email']}"
    payload["triggered_by"] = "web"
    try:
        out = await bridge.inject_inbound_reply(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    new_run_id = out.get("run_id") if isinstance(out, dict) else None
    if new_run_id:
        conn.execute(
            "UPDATE product_campaigns SET run_id=?, status='running' "
            "WHERE campaign_id=? AND env=?",
            (new_run_id, campaign_id, body.env),
        )
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="campaign.inject_reply",
        target=campaign_id,
        payload={
            "kol_identity_id": body.kol_identity_id,
            "intent_hint": body.intent_hint,
            "env": body.env,
        },
    )
    return out


# Goal status values we treat as "the lane's active column". Anything else
# (inactive / completed) falls back to None so the UI bucket stays correct.
_ACTIVE_GOAL_STATES = {"in_progress", "blocked", "awaiting_human"}


def _pick_active_per_lane(lanes: dict) -> dict:
    """Bridge returns ``{lane: [goal_state,...]}``; the console renders a
    single ``goal`` per lane. Pick the first non-inactive goal; otherwise
    the last (most advanced) goal so the column is never empty.
    """
    out: dict = {"commerce": None, "fulfillment": None, "publish": None, "meta": None}
    for lane, states in (lanes or {}).items():
        if not states:
            continue
        active = next((s for s in states if s.get("status") in _ACTIVE_GOAL_STATES), None)
        chosen = active or states[-1]
        out[lane] = {
            "goal": chosen.get("goal"),
            "state": chosen.get("status") or "inactive",
            "missing_facts": chosen.get("missing_facts") or [],
            "blocked_reason": chosen.get("blocking_escalation_id") or None,
        }
    return out


@router.get("/{campaign_id}/lanes")
async def lanes(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("LIVE", pattern="^(LIVE|TEST)$"),
) -> dict:
    """Kanban data feed: per-identity lane snapshot + top-of-page counts.

    Returns ``{campaign_id, lanes: LaneSnapshot[], counts:
    {pending_approvals, open_escalations}}``. Bridge errors → 502.
    """
    try:
        raw = await bridge.get_lanes(campaign_id, env=env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    items_out = []
    for it in raw.get("items", []):
        items_out.append({
            "identity_id": it["identity_id"],
            "handle": it.get("handle") or f"id{it['identity_id']}",
            "candidate_status": it.get("candidate_status"),
            "relationship_status": it.get("relationship_status"),
            "repeat_count": it.get("repeat_count") or 0,
            "last_outcome": it.get("last_outcome"),
            "archived": bool(it.get("archived")),
            "goals": _pick_active_per_lane(it.get("lanes") or {}),
        })
    return {
        "campaign_id": campaign_id,
        "lanes": items_out,
        "counts": raw.get("counts") or {"pending_approvals": 0, "open_escalations": 0},
    }
