"""Encrypt Gmail refresh tokens at rest (Fernet)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def _fernet_keys() -> list[Fernet]:
    """Return Fernet instances — dedicated gmail secret first, then jwt fallback."""
    s = get_settings()
    raw_secrets: list[str] = []
    if s.gmail_token_secret.strip():
        raw_secrets.append(s.gmail_token_secret.strip())
    if s.jwt_secret.strip() and s.jwt_secret.strip() not in raw_secrets:
        raw_secrets.append(s.jwt_secret.strip())
    if not raw_secrets:
        raw_secrets.append("dev-only-change-me")
    out: list[Fernet] = []
    for raw in raw_secrets:
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        out.append(Fernet(key))
    return out


def encrypt_token(plaintext: str) -> str:
    return _fernet_keys()[0].encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    last_exc: Exception | None = None
    for fernet in _fernet_keys():
        try:
            return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise ValueError("no Fernet key configured")
