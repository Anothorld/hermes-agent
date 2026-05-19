"""Password hashing + JWT helpers."""

from __future__ import annotations

import datetime as _dt
from typing import Any

import bcrypt
import jwt

from .config import get_settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def issue_token(*, user_id: int, role: str) -> str:
    s = get_settings()
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + _dt.timedelta(seconds=s.jwt_ttl_sec)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_alg)


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_alg])
