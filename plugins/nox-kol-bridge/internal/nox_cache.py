"""Monthly SQLite cache for Nox CLI responses + alias index."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from schemas import (
    DEFAULT_RETAIN_MONTHS,
    DEFAULT_TIMEZONE,
    RESPONSE_BLOB_THRESHOLD_BYTES,
)
from internal.paths import nox_cache_db_path, nox_cache_root
from internal.normalize import alias_key


def _connect() -> sqlite3.Connection:
    path = nox_cache_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries (
            cache_month TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            response_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (cache_month, cache_key)
        );
        CREATE TABLE IF NOT EXISTS alias (
            alias_key TEXT PRIMARY KEY,
            nox_creator_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stats (
            cache_month TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            misses INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (cache_month)
        );
        """
    )
    conn.commit()


def _blob_path_for(cache_month: str, cache_key: str) -> Path:
    digest = hashlib.sha256(f"{cache_month}:{cache_key}".encode()).hexdigest()[:32]
    root = nox_cache_root() / "blobs" / cache_month
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.json"


def _encode_response(cache_month: str, cache_key: str, response: dict[str, Any]) -> str:
    """Persist large responses on disk; store pointer in SQLite."""
    raw = json.dumps(response, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= RESPONSE_BLOB_THRESHOLD_BYTES:
        return raw
    path = _blob_path_for(cache_month, cache_key)
    path.write_text(raw, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return json.dumps({"_blob_ref": str(path)})


def _decode_response(stored_json: str) -> dict[str, Any]:
    data = json.loads(stored_json)
    if isinstance(data, dict) and isinstance(data.get("_blob_ref"), str):
        path = Path(data["_blob_ref"])
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def current_cache_month(tz_name: str = DEFAULT_TIMEZONE) -> str:
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).strftime("%Y-%m")


def lookup(
    cache_month: str,
    cache_key: str,
    *,
    tz_name: str = DEFAULT_TIMEZONE,
) -> Optional[dict[str, Any]]:
    """Return stored envelope or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT response_json, fetched_at, operation FROM entries "
            "WHERE cache_month = ? AND cache_key = ?",
            (cache_month, cache_key),
        ).fetchone()
        if row is None:
            _bump_stat(conn, cache_month, miss=True)
            conn.commit()
            return None
        _bump_stat(conn, cache_month, miss=False)
        conn.commit()
        return {
            "cache_hit": True,
            "cache_month": cache_month,
            "cache_key": cache_key,
            "fetched_at": row["fetched_at"],
            "operation": row["operation"],
            "response": _decode_response(row["response_json"]),
            "api_calls": 0,
        }


def store(
    cache_month: str,
    cache_key: str,
    operation: str,
    response: dict[str, Any],
) -> None:
    fetched_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload = _encode_response(cache_month, cache_key, response)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(cache_month, cache_key, operation, response_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cache_month, cache_key, operation, payload, fetched_at),
        )
        conn.commit()
    prune_old_months(DEFAULT_RETAIN_MONTHS, tz_name=DEFAULT_TIMEZONE)


def resolve_alias(platform: str, handle_or_url: str) -> Optional[str]:
    key = alias_key(platform, handle_or_url)
    with _connect() as conn:
        row = conn.execute(
            "SELECT nox_creator_id FROM alias WHERE alias_key = ?", (key,)
        ).fetchone()
    return row["nox_creator_id"] if row else None


def put_alias(platform: str, handle_or_url: str, nox_creator_id: str) -> None:
    key = alias_key(platform, handle_or_url)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO alias (alias_key, nox_creator_id, updated_at) "
            "VALUES (?, ?, ?)",
            (key, nox_creator_id, now),
        )
        conn.commit()


def cache_stats(cache_month: Optional[str] = None, *, tz_name: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
    month = cache_month or current_cache_month(tz_name)
    with _connect() as conn:
        row = conn.execute(
            "SELECT hits, misses FROM stats WHERE cache_month = ?", (month,)
        ).fetchone()
        entry_count = conn.execute(
            "SELECT COUNT(*) AS c FROM entries WHERE cache_month = ?", (month,)
        ).fetchone()["c"]
    hits = int(row["hits"]) if row else 0
    misses = int(row["misses"]) if row else 0
    return {
        "cache_month": month,
        "hits": hits,
        "misses": misses,
        "entries": entry_count,
        "saved_api_calls_estimate": hits,
    }


def _bump_stat(conn: sqlite3.Connection, cache_month: str, *, miss: bool) -> None:
    conn.execute(
        "INSERT INTO stats (cache_month, hits, misses) VALUES (?, 0, 0) "
        "ON CONFLICT(cache_month) DO NOTHING",
        (cache_month,),
    )
    if miss:
        conn.execute(
            "UPDATE stats SET misses = misses + 1 WHERE cache_month = ?",
            (cache_month,),
        )
    else:
        conn.execute(
            "UPDATE stats SET hits = hits + 1 WHERE cache_month = ?",
            (cache_month,),
        )


def prune_old_months(retain: int, *, tz_name: str = DEFAULT_TIMEZONE) -> int:
    """Delete entry rows older than retain months (by YYYY-MM sort)."""
    month = current_cache_month(tz_name)
    y, m = int(month[:4]), int(month[5:7])
    keep = {month}
    for _ in range(retain - 1):
        m -= 1
        if m < 1:
            m = 12
            y -= 1
        keep.add(f"{y:04d}-{m:02d}")
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT cache_month FROM entries").fetchall()
        deleted = 0
        for r in rows:
            cm = r["cache_month"]
            if cm not in keep:
                cur = conn.execute(
                    "DELETE FROM entries WHERE cache_month = ?", (cm,)
                )
                deleted += cur.rowcount
        conn.commit()
    return deleted
