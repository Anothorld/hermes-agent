"""Proxy routes for campaign candidates (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role
from ..discovery_feedback import (
    DecisionFeedbackBody,
    get_product_info,
    record_decisions_safe,
    validate_decision_feedback,
)

router = APIRouter(prefix="/campaigns/{campaign_id}/candidates", tags=["candidates"])

# review_reason marker the shortlist UI sends for an operator removal —
# only this path requires decision-learning feedback (agent/automated
# rejected writes stay untouched).
SHORTLIST_REMOVAL_REASON = "operator_removed_from_shortlist"


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class UpsertCandidateBody(BaseModel):
    identity_id: int
    discovery_score: Optional[float] = None
    discovery_source: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    env: Optional[str] = None


class SelectCandidatesBody(BaseModel):
    identity_ids: list[int] = Field(min_length=1)
    env: Optional[str] = None


class SetCandidateStatusBody(BaseModel):
    identity_ids: list[int] = Field(min_length=1)
    candidate_status: str = Field(
        pattern="^(discovered|shortlisted|selected_for_outreach|needs_review|rejected|archived)$"
    )
    review_reason: Optional[str] = Field(default=None, max_length=500)
    env: Optional[str] = None
    # Decision-learning feedback (required for operator shortlist removals).
    reason_tags: list[str] = Field(default_factory=list)
    comment: Optional[str] = Field(default=None, max_length=2000)


def _shape_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map bridge candidate+handle row to ``CampaignCandidatesPage`` shape."""
    cs = str(row.get("candidate_status") or "")
    if cs == "selected_for_outreach":
        status = "selected"
    elif cs in {"rejected", "archived"}:
        status = "rejected"
    else:
        status = "pending"
    return {
        "identity_id": row.get("identity_id"),
        "handle": row.get("handle"),
        "discovery_score": row.get("discovery_score"),
        "discovery_source": row.get("source"),
        "relationship_status": row.get("relationship_status"),
        "total_collabs": int(row.get("total_collabs") or 0),
        "last_outcome": row.get("last_outcome"),
        "status": status,
        "notes": row.get("review_reason"),
    }


@router.get("")
async def list_candidates(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: Optional[str] = Query(None),
) -> list[dict[str, Any]]:
    try:
        rows = await bridge.list_candidate_handles(campaign_id, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return [_shape_candidate_row(r) for r in rows if isinstance(r, dict)]


@router.post("")
async def upsert_candidate(
    campaign_id: str,
    body: UpsertCandidateBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.upsert_candidate(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="candidate.upsert",
        target=f"{campaign_id}:{body.identity_id}",
        payload={"discovery_score": body.discovery_score},
    )
    return out


@router.post("/resolve-relationships")
async def resolve_relationships(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
    env: Optional[str] = Query(None),
) -> dict[str, Any]:
    try:
        out = await bridge.resolve_relationships(campaign_id, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="candidate.resolve_relationships",
        target=campaign_id, payload={},
    )
    return out


@router.post("/select")
async def select_candidates(
    campaign_id: str,
    body: SelectCandidatesBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.select_candidates(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="candidate.select",
        target=campaign_id,
        payload={"identity_ids": body.identity_ids},
    )
    return out


@router.post("/status")
async def set_candidate_status(
    campaign_id: str,
    body: SetCandidateStatusBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    env = _env(body.env)
    is_shortlist_removal = (
        body.candidate_status == "rejected"
        and body.review_reason == SHORTLIST_REMOVAL_REASON
    )
    product_info: dict[str, Any] = {}
    if is_shortlist_removal:
        product_info = get_product_info(conn, campaign_id=campaign_id, env=env)
        # Best-effort handle lookup so 422 details name @handles, not raw ids.
        handle_labels: list[str] = [str(i) for i in body.identity_ids]
        try:
            briefs = await bridge.batch_identity_briefs(body.identity_ids)
            handle_labels = [
                str((briefs.get(i) or {}).get("primary_handle") or i)
                for i in body.identity_ids
            ]
        except Exception:  # noqa: BLE001 — labels only; ids are a fine fallback
            pass
        await validate_decision_feedback(
            bridge,
            feedback=DecisionFeedbackBody(
                shared_tags=body.reason_tags,
                shared_comment=body.comment,
            ),
            handles=handle_labels,
            sku=product_info.get("sku"),
            env=env,
            action="remove",
        )
    payload = body.model_dump(exclude_none=True, exclude={"reason_tags", "comment"})
    payload["env"] = env
    try:
        out = await bridge.set_candidate_status(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if is_shortlist_removal:
        learning = await record_decisions_safe(
            bridge, conn,
            campaign_id=campaign_id,
            env=env,
            action="remove",
            decided_by=f"web:{user['email']}",
            actor_user_id=user["id"],
            decisions=[
                {"identity_id": i, "tags": body.reason_tags, "comment": body.comment}
                for i in body.identity_ids
            ],
            product_info=product_info,
        )
        out = {**out, "learning": learning}
    write_audit(
        conn, actor_user_id=user["id"], action="candidate.set_status",
        target=campaign_id,
        payload={
            "identity_ids": body.identity_ids,
            "candidate_status": body.candidate_status,
            "review_reason": body.review_reason,
            "reason_tags": body.reason_tags,
        },
    )
    return out
