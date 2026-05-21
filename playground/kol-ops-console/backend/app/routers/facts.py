"""Proxy routes for facts read/write (Phase C-i).

The bridge enforces fact namespaces and approval policy. The console only
gates by RBAC and adds audit logging.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/facts", tags=["facts"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class FactsWriteBody(BaseModel):
    campaign_id: Optional[str] = None
    facts: dict[str, Any]
    source: str = Field(default="console")
    source_event_id: Optional[int] = None
    env: Optional[str] = None


class FactsWriteMultiBody(BaseModel):
    campaign_id: Optional[str] = None
    namespaces: dict[str, dict[str, Any]]
    source: str = Field(default="console")
    source_event_id: Optional[int] = None
    env: Optional[str] = None


@router.get("/{identity_id}")
async def read_facts(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    campaign_id: Optional[str] = Query(None),
    env: Optional[str] = Query(None),
) -> dict:
    try:
        return await bridge.read_facts(
            identity_id, campaign_id=campaign_id, env=_env(env)
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{identity_id}")
async def write_facts(
    identity_id: int,
    body: FactsWriteBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.write_facts(identity_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="facts.write",
        target=str(identity_id), payload={"keys": list(body.facts.keys())},
    )
    return out


@router.post("/{identity_id}/multi")
async def write_facts_multi(
    identity_id: int,
    body: FactsWriteMultiBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.write_facts_multi(identity_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="facts.write_multi",
        target=str(identity_id),
        payload={"namespaces": list(body.namespaces.keys())},
    )
    return out
