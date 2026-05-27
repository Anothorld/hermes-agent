"""Proxy routes for goal_state per identity + dispatch context (Phase C-i)."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..bridge_client import BridgeClient, BridgeError
from ..campaign_id_norm import NULL_SENTINEL_CAMPAIGN_IDS
from ..config import get_settings
from ..deps import current_user, get_bridge

router = APIRouter(prefix="/identities", tags=["goals"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


def _require_real_campaign_id(campaign_id: str) -> str:
    """Reject sentinel strings on routes that require a real campaign id.

    Returns the trimmed id on success, raises HTTP 400 otherwise. Both
    routes here are read-only proxies, but emitting a clear 400 here
    spares the operator a confusing downstream "campaign not found".
    """
    s = campaign_id.strip()
    if not s or s.lower() in NULL_SENTINEL_CAMPAIGN_IDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "invalid_campaign_id",
                "message": (
                    f"campaign_id query is the sentinel string "
                    f"{campaign_id!r} — the caller built this URL from a "
                    "null/undefined value."
                ),
            },
        )
    return s


@router.get("/{identity_id}/goals")
async def get_goals(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    campaign_id: str = Query(...),
    env: Optional[str] = Query(None),
) -> dict:
    cid = _require_real_campaign_id(campaign_id)
    try:
        return await bridge.get_goals(identity_id, cid, env=_env(env))
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
    cid = _require_real_campaign_id(campaign_id)
    try:
        return await bridge.get_dispatch_context(
            identity_id, cid, env=_env(env)
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
