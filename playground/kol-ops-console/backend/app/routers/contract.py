"""Contract STUB workflow — pushes to bridge ``/contract/update``."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import get_bridge, get_conn, require_role

router = APIRouter(prefix="/contract", tags=["contract"])


class ContractUpdateBody(BaseModel):
    kol_identity_id: int
    sub_status: Literal["pending", "sent_for_signature", "signed", "declined"]
    note: str | None = None
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")


@router.post("/update")
async def update(
    body: ContractUpdateBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    payload = body.model_dump()
    payload["actor"] = f"web:{user['email']}"
    try:
        out = await bridge.push_contract_update(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(conn, actor_user_id=user["id"], action="contract.update",
                target=str(body.kol_identity_id), payload=payload)
    return out
