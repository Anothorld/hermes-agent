"""Monthly SQLite + blob cache for Veedcrawl REST responses."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

DEFAULT_RETAIN_MONTHS = 3
DEFAULT_TIMEZONE = "Asia/Shanghai"
RESPONSE_BLOB_THRESHOLD_BYTES = 100_000


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes"


def veedcrawl_cache_root() -> Path:
    root = hermes_home() / "kol-ops-bridge" / "veedcrawl_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def veedcrawl_cache_db_path() -> Path:
    return veedcrawl_cache_root() / "veedcrawl_cache.db"


_DB_OVERRIDE: Optional[Path] = None


def set_db_path(path: Optional[Path]) -> None:
    """Test hook: override cache DB path."""
    global _DB_OVERRIDE
    _DB_OVERRIDE = path


def _db_path() -> Path:
    return _DB_OVERRIDE or veedcrawl_cache_db_path()


def _connect() -> sqlite3.Connection:
    path = _db_path()
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
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_month TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            cache_hit INTEGER NOT NULL,
            env TEXT NOT NULL,
            identity_id INTEGER,
            fetched_at TEXT NOT NULL
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
    root = veedcrawl_cache_root() / "blobs" / cache_month
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.json"


def _encode_response(cache_month: str, cache_key: str, response: Any) -> str:
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


def _decode_response(stored_json: str) -> Any:
    data = json.loads(stored_json)
    if isinstance(data, dict) and isinstance(data.get("_blob_ref"), str):
        path = Path(data["_blob_ref"])
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return data


def blob_ref_for(cache_month: str, cache_key: str) -> Optional[str]:
    """Return on-disk blob path if the entry is stored externally."""
    digest = hashlib.sha256(f"{cache_month}:{cache_key}".encode()).hexdigest()[:32]
    path = veedcrawl_cache_root() / "blobs" / cache_month / f"{digest}.json"
    return str(path) if path.is_file() else None


def storage_ref_for(cache_month: str, cache_key: str) -> str:
    """Stable pointer for envelope — blob file or inline SQLite key."""
    blob = blob_ref_for(cache_month, cache_key)
    if blob:
        return blob
    return f"sqlite:{cache_month}:{cache_key}"


def current_cache_month(tz_name: str = DEFAULT_TIMEZONE) -> str:
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).strftime("%Y-%m")


def lookup(
    cache_month: str,
    cache_key: str,
    *,
    tz_name: str = DEFAULT_TIMEZONE,
) -> Optional[dict[str, Any]]:
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
        blob_ref = blob_ref_for(cache_month, cache_key)
        return {
            "cache_hit": True,
            "cache_month": cache_month,
            "cache_key": cache_key,
            "fetched_at": row["fetched_at"],
            "operation": row["operation"],
            "response": _decode_response(row["response_json"]),
            "api_calls": 0,
            "blob_ref": blob_ref,
            "storage_ref": storage_ref_for(cache_month, cache_key),
        }


def store(
    cache_month: str,
    cache_key: str,
    operation: str,
    response: Any,
) -> Optional[str]:
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
    return storage_ref_for(cache_month, cache_key)


def log_fetch(
    *,
    cache_month: str,
    cache_key: str,
    operation: str,
    cache_hit: bool,
    env: str,
    identity_id: Optional[int] = None,
) -> None:
    fetched_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO fetch_log "
            "(cache_month, cache_key, operation, cache_hit, env, identity_id, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                cache_month,
                cache_key,
                operation,
                1 if cache_hit else 0,
                env,
                identity_id,
                fetched_at,
            ),
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
    month = current_cache_month(tz_name)
    y, m = int(month[:4]), int(month[5:7])
    keep = {month}
    for _ in range(retain - 1):
        m -= 1
        if m < 1:
            m = 12
            y -= 1
        keep.add(f"{y:04d}-{m:02d}")
    deleted = 0
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT cache_month FROM entries").fetchall()
        for r in rows:
            cm = r["cache_month"]
            if cm not in keep:
                cur = conn.execute(
                    "DELETE FROM entries WHERE cache_month = ?", (cm,)
                )
                deleted += cur.rowcount
        log_rows = conn.execute("SELECT DISTINCT cache_month FROM fetch_log").fetchall()
        for r in log_rows:
            cm = r["cache_month"]
            if cm not in keep:
                conn.execute("DELETE FROM fetch_log WHERE cache_month = ?", (cm,))
        stale_stats = conn.execute("SELECT cache_month FROM stats").fetchall()
        for r in stale_stats:
            cm = r["cache_month"]
            if cm not in keep:
                conn.execute("DELETE FROM stats WHERE cache_month = ?", (cm,))
        conn.commit()
    root = veedcrawl_cache_root() / "blobs"
    if root.is_dir():
        for month_dir in root.iterdir():
            if month_dir.is_dir() and month_dir.name not in keep:
                for path in month_dir.glob("*.json"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                try:
                    month_dir.rmdir()
                except OSError:
                    pass
    return deleted
