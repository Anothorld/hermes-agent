"""Proxy routes for relationship + reusable facts + archive (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/identities", tags=["relationships"])


class ArchiveBody(BaseModel):
    campaign_id: str
    outcome: str
    reusable_facts: dict[str, Any] = {}
    notes: str = ""


@router.get("/{identity_id}/relationship")
async def get_relationship(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
) -> dict:
    try:
        return await bridge.get_relationship(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/{identity_id}/relationship/reusable-facts")
async def get_reusable_facts(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
) -> dict:
    try:
        return await bridge.get_reusable_facts(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{identity_id}/archive")
async def archive_collab(
    identity_id: int,
    body: ArchiveBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    payload = body.model_dump()
    try:
        out = await bridge.archive_collab(identity_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="relationship.archive",
        target=str(identity_id),
        payload={"campaign_id": body.campaign_id, "outcome": body.outcome},
    )
    return out
