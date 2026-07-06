"""Self-contained SQLite for cs-intent-classifier.

Independent from cs-ops-bridge CAL — uses its own ``cs_intent.db`` file.
All writes carry an explicit ``env`` (LIVE/TEST) — never rely on defaults.

Tables:
- cs_intent_classifications — one row per classify call
- cs_intent_corrections — operator label corrections (learning source)
- cs_intent_eval_daily — daily eval pass-rate time series
- cs_learning_job_runs — distill/eval job audit log
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MIGRATED: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cs_intent_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    env TEXT NOT NULL,
    gate_extract_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    classifier_source TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    UNIQUE(session_id, env, classified_at)
);

CREATE INDEX IF NOT EXISTS idx_classifications_session
    ON cs_intent_classifications(session_id, env, classified_at DESC);

CREATE TABLE IF NOT EXISTS cs_intent_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    env TEXT NOT NULL,
    predicted_json TEXT NOT NULL,
    corrected_json TEXT NOT NULL,
    reason TEXT,
    operator_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_corrections_env_created
    ON cs_intent_corrections(env, created_at DESC);

CREATE TABLE IF NOT EXISTS cs_intent_eval_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    env TEXT NOT NULL,
    model_version TEXT NOT NULL,
    accuracy REAL NOT NULL,
    per_intent_json TEXT,
    by_source_json TEXT,
    UNIQUE(date, env, model_version)
);

CREATE TABLE IF NOT EXISTS cs_learning_job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    job TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    payload_json TEXT
);
"""


def _db_path() -> Path:
    """Resolve cs_intent.db path. Honors CS_INTENT_DB_PATH override."""
    explicit = os.environ.get("CS_INTENT_DB_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    # Default: plugin-local data dir
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "cs_intent.db"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a sqlite connection with WAL + foreign keys. Thread-safe via lock."""
    path = _db_path()
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _migrate(conn, str(path))
            yield conn
        finally:
            conn.close()


def _migrate(conn: sqlite3.Connection, key: str) -> None:
    """Idempotent schema migration. Runs once per process per DB path."""
    if key in _MIGRATED:
        return
    conn.executescript(_SCHEMA)
    _MIGRATED.add(key)
    log.debug("cs_intent db migrated at %s", key)


# ── Classifications ──


def insert_classification(
    *,
    session_id: str,
    env: str,
    gate_extract: dict[str, Any],
    model_version: str,
    classifier_source: str,
) -> str:
    """Persist one classification. Returns classified_at timestamp."""
    ts = _utcnow()
    with connect() as conn:
        conn.execute(
            """INSERT INTO cs_intent_classifications
               (session_id, env, gate_extract_json, model_version, classifier_source, classified_at)
               VALUES (?,?,?,?,?,?)""",
            (session_id, env, json.dumps(gate_extract, ensure_ascii=False), model_version, classifier_source, ts),
        )
    return ts


def latest_classification(*, session_id: str, env: str) -> Optional[dict[str, Any]]:
    """Fetch the most recent classification for a session."""
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM cs_intent_classifications
               WHERE session_id=? AND env=? ORDER BY classified_at DESC LIMIT 1""",
            (session_id, env),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["gate_extract"] = json.loads(d.pop("gate_extract_json"))
    return d


# ── Corrections ──


def insert_correction(
    *,
    session_id: str,
    env: str,
    predicted: dict[str, Any],
    corrected: dict[str, Any],
    reason: str,
    operator_id: str,
) -> int:
    """Persist an operator correction. Returns the correction row id."""
    ts = _utcnow()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO cs_intent_corrections
               (session_id, env, predicted_json, corrected_json, reason, operator_id, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                session_id,
                env,
                json.dumps(predicted, ensure_ascii=False),
                json.dumps(corrected, ensure_ascii=False),
                reason,
                operator_id,
                ts,
            ),
        )
        return int(cur.lastrowid)


def list_corrections(
    *, env: str, since: str = "", until: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    """List corrections for learning, ordered newest-first."""
    with connect() as conn:
        sql = "SELECT * FROM cs_intent_corrections WHERE env=?"
        params: list[Any] = [env]
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        if until:
            sql += " AND created_at <= ?"
            params.append(until)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["predicted"] = json.loads(d.pop("predicted_json"))
        d["corrected"] = json.loads(d.pop("corrected_json"))
        out.append(d)
    return out


def corrections_for_session(*, session_id: str, env: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM cs_intent_corrections
               WHERE session_id=? AND env=? ORDER BY created_at DESC""",
            (session_id, env),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["predicted"] = json.loads(d.pop("predicted_json"))
        d["corrected"] = json.loads(d.pop("corrected_json"))
        out.append(d)
    return out


# ── Eval daily time series ──


def record_eval_snapshot(
    *,
    date: str,
    env: str,
    model_version: str,
    accuracy: float,
    per_intent: Optional[dict[str, Any]] = None,
    by_source: Optional[dict[str, Any]] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO cs_intent_eval_daily
               (date, env, model_version, accuracy, per_intent_json, by_source_json)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(date, env, model_version) DO UPDATE SET
                   accuracy=excluded.accuracy,
                   per_intent_json=excluded.per_intent_json,
                   by_source_json=excluded.by_source_json""",
            (
                date,
                env,
                model_version,
                accuracy,
                json.dumps(per_intent or {}, ensure_ascii=False),
                json.dumps(by_source or {}, ensure_ascii=False),
            ),
        )


def eval_trend(*, env: str, days: int = 14) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM cs_intent_eval_daily
               WHERE env=? ORDER BY date DESC LIMIT ?""",
            (env, days),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["per_intent"] = json.loads(d.pop("per_intent_json") or "{}")
        d["by_source"] = json.loads(d.pop("by_source_json") or "{}")
        out.append(d)
    return out


# ── Learning job audit ──


def record_job_run(
    *,
    env: str,
    job: str,
    started_at: str,
    finished_at: Optional[str],
    status: str,
    payload: Optional[dict[str, Any]] = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO cs_learning_job_runs
               (env, job, started_at, finished_at, status, payload_json)
               VALUES (?,?,?,?,?,?)""",
            (env, job, started_at, finished_at or "", status, json.dumps(payload or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def list_job_runs(*, env: str, job: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        sql = "SELECT * FROM cs_learning_job_runs WHERE env=?"
        params: list[Any] = [env]
        if job:
            sql += " AND job=?"
            params.append(job)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
        out.append(d)
    return out
