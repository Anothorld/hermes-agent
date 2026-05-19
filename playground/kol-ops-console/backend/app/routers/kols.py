"""KOL list + detail (merge bridge identities with local notes)."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role

router = APIRouter(prefix="/kols", tags=["kols"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


@router.get("")
async def list_kols(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> list[dict]:
    try:
        return await bridge.list_identities(_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/{identity_id}")
async def get_kol(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    try:
        identity = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    notes = conn.execute(
        "SELECT id, body, author_user_id, created_at FROM kol_notes "
        "WHERE kol_identity_id=? ORDER BY created_at DESC",
        (identity_id,),
    ).fetchall()
    identity["notes"] = [dict(n) for n in notes]
    return identity


@router.get("/{identity_id}/timeline")
async def get_timeline(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    try:
        return await bridge.get_timeline(identity_id, _env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/{identity_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(
    identity_id: int,
    body: NoteBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    conn.execute(
        "INSERT INTO kol_notes (kol_identity_id, author_user_id, body, created_at) VALUES (?,?,?,?)",
        (identity_id, user["id"], body.body,
         _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
    )
    write_audit(conn, actor_user_id=user["id"], action="kol.note.add", target=str(identity_id))
    return {"ok": True}
