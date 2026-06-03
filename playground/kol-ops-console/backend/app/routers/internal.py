"""Internal APIs for kol-ops-bridge and background pollers (shared secret)."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from ..bridge_runtime import resolve_bridge_key
from ..config import get_settings
from ..deps import get_conn
from ..gmail_store import ensure_token_file_fresh, list_active_connections

router = APIRouter(prefix="/internal", tags=["internal"])


def _internal_key(
    x_internal_key: Annotated[str | None, Header(alias="X-Internal-Key")] = None,
    x_bridge_key: Annotated[str | None, Header(alias="X-Bridge-Key")] = None,
) -> None:
    s = get_settings()
    expected = (s.internal_api_key or s.bridge_key or resolve_bridge_key(s) or "").strip()
    if not expected:
        return
    got = (x_internal_key or x_bridge_key or "").strip()
    if got != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal key")


@router.get("/gmail-connections")
def list_gmail_connections(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[None, Depends(_internal_key)],
) -> dict:
    items = []
    for row in list_active_connections(conn):
        uid = int(row["user_id"])
        path = ensure_token_file_fresh(conn, uid)
        items.append({
            "user_id": uid,
            "google_email": row["google_email"],
            "token_path": str(path) if path else None,
            "connected_at": row["connected_at"],
        })
    return {"connections": items, "count": len(items)}


@router.post("/gmail-connections/{user_id}/token-file")
def refresh_gmail_token_file(
    user_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[None, Depends(_internal_key)],
) -> dict:
    """Materialize encrypted Gmail token to disk for bridge/poller hosts."""
    path = ensure_token_file_fresh(conn, user_id)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active gmail connection")
    return {"user_id": user_id, "token_path": str(path), "ok": True}


@router.get("/users/resolve")
def resolve_console_user(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[None, Depends(_internal_key)],
    email: str | None = None,
    user_id: int | None = None,
) -> dict:
    """Map Console user email or id (for bridge CLI/gateway approve)."""
    if user_id is not None and user_id > 0:
        row = conn.execute(
            "SELECT id, email, role FROM users WHERE id=? AND is_active=1",
            (int(user_id),),
        ).fetchone()
    elif email:
        row = conn.execute(
            "SELECT id, email, role FROM users WHERE lower(email)=? AND is_active=1",
            (email.strip().lower(),),
        ).fetchone()
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email or user_id required")
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return {"user_id": int(row["id"]), "email": row["email"], "role": row["role"]}
