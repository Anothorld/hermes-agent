"""Proxy routes for escalations list / open / resolve (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/escalations", tags=["escalations"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class OpenEscalationBody(BaseModel):
    identity_id: int
    campaign_id: str
    rule_id: Optional[str] = None
    reason: str = Field(min_length=1, max_length=2000)
    suggested_question: Optional[str] = None
    parent_id: Optional[int] = None
    env: Optional[str] = None


class ResolveEscalationBody(BaseModel):
    decision: str = Field(pattern="^(resume|terminate)$")
    operator_answer: str = Field(min_length=0, max_length=4000, default="")
    operator_facts: dict[str, Any] = {}
    final_state: Optional[str] = None


@router.get("")
async def list_escalations(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    state: Optional[str] = Query(None),
    env: Optional[str] = Query(None),
) -> list[dict]:
    try:
        return await bridge.list_escalations(state=state, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
async def open_escalation(
    body: OpenEscalationBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.open_escalation(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="escalation.open",
        target=str(body.identity_id),
        payload={"rule_id": body.rule_id, "campaign_id": body.campaign_id},
    )
    return out


@router.patch("/{escalation_id}")
async def resolve_escalation(
    escalation_id: int,
    body: ResolveEscalationBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    payload = body.model_dump(exclude_none=True)
    try:
        out = await bridge.resolve_escalation(escalation_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="escalation.resolve",
        target=str(escalation_id),
        payload={"decision": body.decision},
    )
    return out
