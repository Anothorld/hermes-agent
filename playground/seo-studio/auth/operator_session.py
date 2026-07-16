"""Signed-cookie session for SEO Studio operators."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "seo_studio_session"
TTL_SEC = int(os.environ.get("SEO_STUDIO_SESSION_TTL_SEC", "28800"))


def cookie_secure() -> bool:
    return os.environ.get("SEO_STUDIO_COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no")


def _secret() -> str:
    return os.environ.get("SEO_STUDIO_SESSION_SECRET", "")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="seo-studio-session")


@dataclass
class SessionToken:
    token: str
    operator_id: int
    oidc_sub: str
    name: str
    issued_at: int


def create(*, operator_id: int, oidc_sub: str, name: str) -> SessionToken:
    if not _secret():
        raise RuntimeError("SEO_STUDIO_SESSION_SECRET is empty — cannot issue session")
    issued = int(time.time())
    payload = {"operator_id": operator_id, "oidc_sub": oidc_sub, "name": name, "issued_at": issued}
    token = _serializer().dumps(payload)
    return SessionToken(token=token, operator_id=operator_id, oidc_sub=oidc_sub, name=name, issued_at=issued)


def verify(cookie_value: Optional[str]) -> Optional[dict]:
    if not cookie_value or not _secret():
        return None
    try:
        payload = _serializer().loads(cookie_value, max_age=TTL_SEC)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(payload, dict) or "operator_id" not in payload:
        return None
    return payload
