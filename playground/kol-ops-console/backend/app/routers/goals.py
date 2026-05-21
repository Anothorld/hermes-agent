"""Proxy routes for goal_state per identity + dispatch context (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge

router = APIRouter(prefix="/identities", tags=["goals"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


@router.get("/{identity_id}/goals")
async def get_goals(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    campaign_id: str = Query(...),
    env: Optional[str] = Query(None),
) -> dict:
    try:
        return await bridge.get_goals(identity_id, campaign_id, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/{identity_id}/dispatch-context")
async def get_dispatch_context(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    campaign_id: str = Query(...),
    env: Optional[str] = Query(None),
) -> dict:
    try:
        return await bridge.get_dispatch_context(
            identity_id, campaign_id, env=_env(env)
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
