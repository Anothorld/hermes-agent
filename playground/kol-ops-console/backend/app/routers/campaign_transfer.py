"""Campaign transfer routes (Phase 1a: shortlist pre-approval)."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import get_bridge, get_conn, require_role

router = APIRouter(prefix="/identities", tags=["campaign-transfer"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class TransferCampaignBody(BaseModel):
    from_campaign_id: str = Field(min_length=1)
    to_campaign_id: str = Field(min_length=1)
    env: Optional[str] = Field(default=None, pattern="^(LIVE|TEST)$")
    source_stage: str = Field(default="shortlist", pattern="^shortlist$")
    reason: str = Field(default="", max_length=500)
    operator_note: str = Field(default="", max_length=500)


@router.post("/{identity_id}/transfer-campaign")
async def transfer_campaign(
    identity_id: int,
    body: TransferCampaignBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    """Move a KOL from one campaign shortlist to another (before approve)."""
    payload = {
        "from_campaign_id": body.from_campaign_id,
        "to_campaign_id": body.to_campaign_id,
        "env": _env(body.env),
        "source_stage": body.source_stage,
        "reason": body.reason,
        "operator_note": body.operator_note,
    }
    try:
        out = await bridge.transfer_campaign(identity_id, payload)
    except BridgeError as exc:
        if exc.status >= 500:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="campaign.transfer_shortlist",
        target=f"{identity_id}:{body.from_campaign_id}->{body.to_campaign_id}",
        payload={
            "identity_id": identity_id,
            "from_campaign_id": body.from_campaign_id,
            "to_campaign_id": body.to_campaign_id,
            "env": payload["env"],
            "reason": body.reason,
        },
    )
    return out
