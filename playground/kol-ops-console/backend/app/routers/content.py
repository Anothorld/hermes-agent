"""Content verdict (approve / revise) — pushes to bridge ``/content/verdict``."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import get_bridge, get_conn, require_role

router = APIRouter(prefix="/content", tags=["content"])


class VerdictBody(BaseModel):
    kol_identity_id: int
    submission_version: int = Field(ge=1)
    verdict: Literal["approve", "revise"]
    revision_notes: str | None = None
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")

    @model_validator(mode="after")
    def _check_notes(self) -> "VerdictBody":
        if self.verdict == "revise" and not (self.revision_notes or "").strip():
            raise ValueError("revision_notes required when verdict=revise")
        return self


@router.post("/verdict")
async def verdict(
    body: VerdictBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    payload = body.model_dump()
    payload["actor"] = f"web:{user['email']}"
    try:
        out = await bridge.push_content_verdict(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(conn, actor_user_id=user["id"], action=f"content.{body.verdict}",
                target=str(body.kol_identity_id), payload=payload)
    return out
