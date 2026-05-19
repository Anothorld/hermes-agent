"""Login / refresh / me."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from ..audit import write_audit
from ..deps import current_user, get_conn
from ..security import hash_password, issue_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int


class CreateUserBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(pattern="^(owner|operator|viewer)$")


@router.post("/login", response_model=TokenResponse)
def login(body: LoginBody, conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> TokenResponse:
    row = conn.execute(
        "SELECT id, password_hash, role, is_active FROM users WHERE email=?",
        (body.email.lower(),),
    ).fetchone()
    if not row or not row["is_active"] or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    write_audit(conn, actor_user_id=row["id"], action="login")
    return TokenResponse(
        access_token=issue_token(user_id=row["id"], role=row["role"]),
        role=row["role"],
        user_id=row["id"],
    )


@router.get("/me")
def me(user: Annotated[dict, Depends(current_user)]) -> dict:
    return {"id": user["id"], "email": user["email"], "role": user["role"]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    if user["role"] != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner only")
    try:
        conn.execute(
            "INSERT INTO users (email, password_hash, role, is_active, created_at) VALUES (?,?,?,1,?)",
            (
                body.email.lower(),
                hash_password(body.password),
                body.role,
                _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "email exists") from None
    write_audit(conn, actor_user_id=user["id"], action="user.create", target=body.email)
    return {"ok": True}
