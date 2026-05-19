"""Admin: TEST wipe + audit log read."""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..bridge_client import BridgeClient, BridgeError
from ..deps import get_bridge, get_conn, require_role

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/wipe-test")
async def wipe_test(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner"))],
) -> dict:
    try:
        return await bridge.wipe_test()
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/audit")
def audit(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(require_role("owner"))],
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
        out.append(d)
    return out
