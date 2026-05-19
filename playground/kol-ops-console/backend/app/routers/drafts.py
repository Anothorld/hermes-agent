"""Drafts queue passthrough (Hermes is the source of truth)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge

router = APIRouter(prefix="/drafts", tags=["drafts"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


@router.get("/pending")
async def list_pending(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> list[dict]:
    try:
        return await bridge.list_pending_drafts(_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/{draft_id}")
async def get_one(
    draft_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    try:
        return await bridge.get_draft(draft_id, _env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
