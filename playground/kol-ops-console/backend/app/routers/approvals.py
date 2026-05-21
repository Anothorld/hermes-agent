"""Proxy routes for cross-cutting approvals list (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class DecisionBody(BaseModel):
    identity_id: int
    campaign_id: str
    decided_by: str = Field(min_length=1, max_length=120)
    note: Optional[str] = Field(default=None, max_length=1000)
    env: Optional[str] = None


@router.get("")
async def list_approvals(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    status_filter: str = Query("pending", alias="status"),
    env: Optional[str] = Query(None),
) -> list[dict[str, Any]]:
    try:
        return await bridge.list_approvals(status=status_filter, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{fact_path:path}/approve")
async def approve(
    fact_path: str,
    body: DecisionBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.approve(fact_path, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="approval.approve",
        target=fact_path,
        payload={"identity_id": body.identity_id, "campaign_id": body.campaign_id},
    )
    return out


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
    write_audit(
        conn, actor_user_id=user["id"], action="approval.reject",
        target=fact_path,
        payload={"identity_id": body.identity_id, "campaign_id": body.campaign_id,
                 "note": body.note},
    )
    return out
