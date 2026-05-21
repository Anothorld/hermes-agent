"""Proxy routes for policy_documents (Phase C-i).

RBAC matrix:
- GET company_style / escalation_rules / their history / parsed rules: any
  authenticated user.
- GET user_style?owner_user_id=<>: owner can read any; operator can read only
  own; viewer can read only own.
- PUT company_style / escalation_rules: owner only.
- PUT user_style?owner_user_id=<>: owner can write any; operator can write
  only own; viewer cannot write.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, get_conn

router = APIRouter(prefix="/policies", tags=["policies"])


_USER_SCOPE = "user_style"
_GLOBAL_SCOPES = {"company_style", "escalation_rules"}


def _check_user_scope_read(user: dict, owner_user_id: Optional[int]) -> int:
    if owner_user_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "owner_user_id required for user_style",
        )
    if user["role"] == "owner":
        return owner_user_id
    if owner_user_id != user["id"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "can only access your own user_style",
        )
    return owner_user_id


def _check_user_scope_write(user: dict, owner_user_id: Optional[int]) -> int:
    if user["role"] == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "viewer cannot write")
    return _check_user_scope_read(user, owner_user_id)


class PolicyPutBody(BaseModel):
    content_md: str = Field(min_length=0, max_length=200_000)
    owner_user_id: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=200)


@router.get("/{scope}")
async def get_policy(
    scope: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(current_user)],
    owner_user_id: Optional[int] = Query(None),
) -> dict:
    if scope == _USER_SCOPE:
        owner = _check_user_scope_read(user, owner_user_id)
        try:
            return await bridge.get_policy(scope, owner_user_id=owner)
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if scope not in _GLOBAL_SCOPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scope")
    try:
        return await bridge.get_policy(scope)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.put("/{scope}")
async def put_policy(
    scope: str,
    body: PolicyPutBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(current_user)],
    conn=Depends(get_conn),
) -> dict:
    payload: dict = {
        "content_md": body.content_md,
        "updated_by": user["email"],
    }
    if body.title is not None:
        payload["title"] = body.title
    if scope == _USER_SCOPE:
        owner = _check_user_scope_write(user, body.owner_user_id)
        payload["owner_user_id"] = owner
    elif scope in _GLOBAL_SCOPES:
        if user["role"] != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"only owner can write {scope}",
            )
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scope")
    try:
        out = await bridge.put_policy(scope, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action=f"policy.{scope}.put",
        target=f"{scope}:{body.owner_user_id or '-'}",
        payload={"version": out.get("policy", {}).get("version")},
    )
    return out


@router.get("/{scope}/history")
async def policy_history(
    scope: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(current_user)],
    owner_user_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    if scope == _USER_SCOPE:
        owner = _check_user_scope_read(user, owner_user_id)
        try:
            return await bridge.policy_history(
                scope, owner_user_id=owner, limit=limit
            )
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if scope not in _GLOBAL_SCOPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scope")
    try:
        return await bridge.policy_history(scope, limit=limit)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/escalation_rules/parsed")
async def parsed_escalation_rules(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
) -> dict:
    try:
        return await bridge.parsed_escalation_rules()
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
