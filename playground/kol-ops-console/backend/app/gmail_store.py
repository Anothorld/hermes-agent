"""Gmail OAuth connections — SQLite + on-disk token files for the bridge."""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .config import get_settings
from .gmail_token_crypto import decrypt_token, encrypt_token

# Subset required for KOL ops (matches kol-ops-bridge usage).
GMAIL_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
)


def tokens_dir() -> Path:
    path = get_settings().gmail_tokens_dir.expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_file_path(user_id: int) -> Path:
    return tokens_dir() / f"{int(user_id)}.json"


def legacy_token_file() -> Path:
    return tokens_dir() / "legacy.json"


def resolve_client_secret_path() -> Path:
    s = get_settings()
    if s.google_client_secret_path:
        return s.google_client_secret_path.expanduser()
    hermes = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    default = hermes / "google_client_secret.json"
    if default.exists():
        return default
    cert = Path(__file__).resolve().parents[5] / "cert"
    if cert.is_dir():
        for p in sorted(cert.glob("client_secret*.json")):
            return p
    return default


def upsert_connection(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    google_email: str,
    token_json: dict[str, Any],
    scopes: list[str],
) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    payload = json.dumps(token_json, ensure_ascii=False)
    encrypted = encrypt_token(payload)
    scopes_json = json.dumps(scopes)
    conn.execute(
        """
        INSERT INTO gmail_connections (
            user_id, google_email, token_encrypted, scopes_json,
            connected_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, NULL)
        ON CONFLICT(user_id) DO UPDATE SET
            google_email=excluded.google_email,
            token_encrypted=excluded.token_encrypted,
            scopes_json=excluded.scopes_json,
            connected_at=excluded.connected_at,
            revoked_at=NULL
        """,
        (user_id, google_email.lower(), encrypted, scopes_json, now),
    )
    _write_token_file(user_id, token_json)


def revoke_connection(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT user_id FROM gmail_connections WHERE user_id=? AND revoked_at IS NULL",
        (user_id,),
    ).fetchone()
    if not row:
        return False
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE gmail_connections SET revoked_at=? WHERE user_id=?",
        (now, user_id),
    )
    path = token_file_path(user_id)
    if path.exists():
        path.unlink()
    return True


def get_connection(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    active_only: bool = True,
) -> Optional[dict[str, Any]]:
    sql = (
        "SELECT user_id, google_email, token_encrypted, scopes_json, "
        "connected_at, revoked_at FROM gmail_connections WHERE user_id=?"
    )
    if active_only:
        sql += " AND revoked_at IS NULL"
    row = conn.execute(sql, (user_id,)).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_active_connections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT user_id, google_email, token_encrypted, scopes_json,
               connected_at, revoked_at
        FROM gmail_connections
        WHERE revoked_at IS NULL
        ORDER BY user_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = _row_to_dict(row)
        d["token_path"] = str(token_file_path(int(d["user_id"])))
        out.append(d)
    return out


def get_token_json(conn: sqlite3.Connection, user_id: int) -> Optional[dict[str, Any]]:
    row = get_connection(conn, user_id, active_only=True)
    if not row:
        return None
    try:
        return json.loads(decrypt_token(row["token_encrypted"]))
    except (json.JSONDecodeError, ValueError):
        return None


def ensure_token_file_fresh(conn: sqlite3.Connection, user_id: int) -> Optional[Path]:
    """Materialize token file from DB if missing."""
    path = token_file_path(user_id)
    if path.exists():
        return path
    token = get_token_json(conn, user_id)
    if not token:
        return None
    _write_token_file(user_id, token)
    return path


def _write_token_file(user_id: int, token_json: dict[str, Any]) -> Path:
    path = token_file_path(user_id)
    path.write_text(json.dumps(token_json, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    scopes: list[str] = []
    try:
        scopes = json.loads(row["scopes_json"] or "[]")
    except json.JSONDecodeError:
        scopes = []
    return {
        "user_id": int(row["user_id"]),
        "google_email": row["google_email"],
        "token_encrypted": row["token_encrypted"],
        "scopes_json": row["scopes_json"],
        "scopes": scopes,
        "connected_at": row["connected_at"],
        "revoked_at": row["revoked_at"],
    }


def migrate_legacy_global_token(conn: sqlite3.Connection, owner_user_id: int) -> bool:
    """Import ~/.hermes/google_token.json as the owner's connection if present."""
    hermes = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    legacy = hermes / "google_token.json"
    if not legacy.exists():
        return False
    if get_connection(conn, owner_user_id, active_only=True):
        return False
    try:
        token_json = json.loads(legacy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    email = _profile_email_from_token(token_json)
    if not email:
        email = f"legacy-user-{owner_user_id}@imported.local"
    scopes = token_json.get("scopes") or list(GMAIL_SCOPES)
    if isinstance(scopes, str):
        scopes = scopes.split()
    upsert_connection(
        conn,
        user_id=owner_user_id,
        google_email=email,
        token_json=token_json,
        scopes=list(scopes),
    )
    return True


def _profile_email_from_token(token_json: dict[str, Any]) -> Optional[str]:
    """Best-effort email from token or a live profile call."""
    for key in ("account", "email", "client_email"):
        val = token_json.get(key)
        if isinstance(val, str) and "@" in val:
            return val.lower()
    path = token_file_path(0)
    return None
