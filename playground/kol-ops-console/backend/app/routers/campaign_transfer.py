"""Campaign transfer routes (Phase 1a: shortlist pre-approval)."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import get_bridge, get_conn, require_role
from ..discovery_feedback import (
    DecisionFeedbackBody,
    get_product_info,
    record_decisions_safe,
    validate_decision_feedback,
)

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
    # Decision-learning feedback; ``reason`` doubles as the comment.
    reason_tags: list[str] = Field(default_factory=list)


@router.post("/{identity_id}/transfer-campaign")
async def transfer_campaign(
    identity_id: int,
    body: TransferCampaignBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    """Move a KOL from one campaign shortlist to another (before approve)."""
    env = _env(body.env)
    product_info = get_product_info(
        conn, campaign_id=body.from_campaign_id, env=env,
    )
    try:
        briefs = await bridge.batch_identity_briefs([identity_id])
        handle = str(
            (briefs.get(identity_id) or {}).get("primary_handle") or identity_id
        )
    except BridgeError:
        handle = str(identity_id)
    await validate_decision_feedback(
        bridge,
        feedback=DecisionFeedbackBody(
            shared_tags=body.reason_tags,
            shared_comment=body.reason,
        ),
        handles=[handle],
        sku=product_info.get("sku"),
        env=env,
        action="transfer",
    )
    payload = {
        "from_campaign_id": body.from_campaign_id,
        "to_campaign_id": body.to_campaign_id,
        "env": env,
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
    learning = await record_decisions_safe(
        bridge, conn,
        campaign_id=body.from_campaign_id,
        env=env,
        action="transfer",
        decided_by=f"web:{user['email']}",
        actor_user_id=user["id"],
        decisions=[{
            "identity_id": identity_id,
            "tags": body.reason_tags,
            "comment": body.reason,
        }],
        product_info=product_info,
        transfer_to_campaign_id=body.to_campaign_id,
    )
    out = {**out, "learning": learning}
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
            "reason_tags": body.reason_tags,
        },
    )
    return out
