"""Proxy routes for campaign candidates (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/campaigns/{campaign_id}/candidates", tags=["candidates"])


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


@router.get("")
async def list_candidates(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: Optional[str] = Query(None),
) -> list[dict[str, Any]]:
    try:
        return await bridge.list_candidates(campaign_id, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


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
