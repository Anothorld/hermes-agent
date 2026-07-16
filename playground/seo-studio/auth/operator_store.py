"""Local operator storage for SEO Studio Feishu login."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _db_path() -> Path:
    return Path(
        os.environ.get(
            "SEO_STUDIO_OPERATOR_DB",
            os.path.expanduser("~/.hermes/seo-studio/operators.db"),
        )
    )


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_operator (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                oidc_sub        TEXT NOT NULL UNIQUE,
                name            TEXT NOT NULL DEFAULT '',
                email           TEXT NOT NULL DEFAULT '',
                first_login_at  TEXT NOT NULL,
                last_login_at   TEXT NOT NULL,
                disabled        INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_studio_operator_sub ON studio_operator(oidc_sub)"
        )


def upsert(*, oidc_sub: str, name: str = "", email: str = "") -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM studio_operator WHERE oidc_sub=?", (oidc_sub,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO studio_operator (oidc_sub, name, email, first_login_at, last_login_at, disabled) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (oidc_sub, name, email, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM studio_operator WHERE oidc_sub=?", (oidc_sub,)
            ).fetchone()
        else:
            conn.execute(
                "UPDATE studio_operator SET last_login_at=?, name=COALESCE(NULLIF(?, ''), name), "
                "email=COALESCE(NULLIF(?, ''), email) WHERE oidc_sub=?",
                (now, name, email, oidc_sub),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM studio_operator WHERE oidc_sub=?", (oidc_sub,)
            ).fetchone()
    return dict(row) if row else {}


def get_by_sub(oidc_sub: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM studio_operator WHERE oidc_sub=?", (oidc_sub,)
        ).fetchone()
    return dict(row) if row else None
