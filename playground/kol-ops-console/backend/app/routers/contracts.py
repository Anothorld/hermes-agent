"""Proxy contract preview/download to kol-ops-bridge."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..deps import current_user, get_bridge
from ..bridge_client import BridgeClient, BridgeError

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("/preview")
async def contract_preview(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    identity_id: int = Query(..., ge=1),
    campaign_id: str = Query(..., min_length=1),
    env: str = Query("LIVE", pattern="^(LIVE|TEST)$"),
    attachment_path: Optional[str] = Query(None),
) -> dict:
    """HTML preview + formal display name for a contract docx."""
    try:
        return await bridge.get_contract_preview(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            attachment_path=attachment_path,
        )
    except BridgeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/download")
async def contract_download(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    identity_id: int = Query(..., ge=1),
    campaign_id: str = Query(..., min_length=1),
    env: str = Query("LIVE", pattern="^(LIVE|TEST)$"),
    attachment_path: Optional[str] = Query(None),
) -> Response:
    """Download contract docx with formal filename."""
    try:
        resp = await bridge.download_contract(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            attachment_path=attachment_path,
        )
    except BridgeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    headers = {"Cache-Control": "no-store"}
    disp = resp.headers.get("content-disposition")
    if disp:
        headers["Content-Disposition"] = disp
    return Response(
        content=resp.content,
        media_type=resp.headers.get(
            "content-type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        headers=headers,
    )
