"""FastAPI dependencies: current_user, role gates, bridge client."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Iterator

from fastapi import Depends, Header, HTTPException, status

from .bridge_client import BridgeClient
from .db import get_conn
from .security import decode_token


_bridge_singleton: BridgeClient | None = None


def get_bridge() -> BridgeClient:
    global _bridge_singleton
    if _bridge_singleton is None:
        _bridge_singleton = BridgeClient()
    return _bridge_singleton


async def shutdown_bridge() -> None:
    global _bridge_singleton
    if _bridge_singleton is not None:
        await _bridge_singleton.aclose()
        _bridge_singleton = None


def current_user(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"bad token: {exc}") from exc
    uid = int(payload["sub"])
    row = conn.execute(
        "SELECT id, email, role, is_active FROM users WHERE id=?", (uid,)
    ).fetchone()
    if not row or not row["is_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")
    return dict(row)


def require_role(*allowed: str):
    """Return a dependency that enforces RBAC."""
    allowed_set = set(allowed)

    def _checker(user: Annotated[dict, Depends(current_user)]) -> dict:
        if user["role"] not in allowed_set:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {user['role']} not allowed")
        return user

    return _checker


__all__ = ["get_bridge", "shutdown_bridge", "current_user", "require_role", "get_conn"]
