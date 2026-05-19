"""SQLite schema + connection helper for the console-local DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner','operator','viewer')),
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS products (
        sku TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT,
        tags_json TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS kol_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kol_identity_id INTEGER NOT NULL,
        author_user_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        actor_user_id INTEGER NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id INTEGER,
        action TEXT NOT NULL,
        target TEXT,
        payload_json TEXT,
        ts TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kol_notes_identity ON kol_notes(kol_identity_id)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_target ON approvals(target_kind, target_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    path = get_settings().db_path
    conn = _connect(path)
    try:
        for ddl in SCHEMA:
            conn.execute(ddl)
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency — one connection per request."""
    conn = _connect(get_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()
