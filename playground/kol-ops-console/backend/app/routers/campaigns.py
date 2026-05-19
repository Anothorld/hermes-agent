"""Start campaigns (proxy to bridge ``/campaigns/{id}/start``)."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import get_bridge, get_conn, require_role

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class StartCampaignBody(BaseModel):
    product_sku: str
    budget_per_kol: float = Field(gt=0)
    absolute_floor: float = Field(gt=0)
    budget_total: float = Field(gt=0)
    headcount_target: int = Field(ge=1, le=200)
    test_mode_to: str
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")


@router.post("/{campaign_id}/start")
async def start(
    campaign_id: str,
    body: StartCampaignBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    payload = body.model_dump()
    payload["triggered_by"] = "web"
    payload["actor"] = f"web:{user['email']}"
    try:
        out = await bridge.start_campaign(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(conn, actor_user_id=user["id"], action="campaign.start",
                target=campaign_id, payload=payload)
    return out
