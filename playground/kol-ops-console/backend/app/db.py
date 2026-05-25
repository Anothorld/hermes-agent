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
        created_at TEXT NOT NULL,
        pitch_md TEXT,
        selling_points TEXT,
        variants_json TEXT,
        default_budget_per_kol REAL,
        default_budget_total REAL,
        default_absolute_floor REAL
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
    # Per-product campaign tracking — written when a Web-initiated Start
    # succeeds. Bridge owns the per-event truth; this table is for "what did
    # the console fire?" + duplicate-trigger guard. Composite PK across env so
    # the same campaign_id may exist in TEST and LIVE.
    """CREATE TABLE IF NOT EXISTS product_campaigns (
        sku TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        env TEXT NOT NULL CHECK (env IN ('LIVE','TEST')),
        run_id TEXT,
        test_mode_to TEXT,
        started_at TEXT NOT NULL,
        started_by_user_id INTEGER,
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running','closed','cancelled')),
        PRIMARY KEY (campaign_id, env)
    )""",
    # Per-campaign run registry. ``product_campaigns.run_id`` only holds the
    # latest outreach run; replies / drafts / resumes each spawn their own
    # gateway run with a distinct session_id. This table is the authoritative
    # list of every run we want the transcript panel to attach to, so the
    # panel can multiplex multiple gateway SSE feeds for one campaign.
    # ``dedup_key`` is the in-flight uniqueness signal for runs that must
    # not be triggered twice while a previous one is still working (currently
    # preview-draft and refine — both write the same approval.reply_draft
    # fact). For runs that are intrinsically idempotent or per-event
    # (outreach launch, reply dispatch), it stays NULL.
    """CREATE TABLE IF NOT EXISTS product_campaign_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL,
        env TEXT NOT NULL CHECK (env IN ('LIVE','TEST')),
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('outreach','reply','draft','resume','refine')),
        session_id TEXT,
        dedup_key TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        UNIQUE (run_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kol_notes_identity ON kol_notes(kol_identity_id)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_target ON approvals(target_kind, target_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_product_campaigns_sku ON product_campaigns(sku, env)",
    "CREATE INDEX IF NOT EXISTS idx_product_campaign_runs_cid ON product_campaign_runs(campaign_id, env, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_product_campaign_runs_dedup ON product_campaign_runs(dedup_key, started_at DESC)",
]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``check_same_thread=False`` is required because FastAPI's threadpool
    # may enter the dependency on one worker thread and run the ``finally``
    # close on another (each request is short-lived and uses a fresh
    # connection, so cross-thread reuse is safe).
    conn = sqlite3.connect(
        str(path), timeout=10.0, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    path = get_settings().db_path
    conn = _connect(path)
    try:
        # Run schema upgrades BEFORE the idempotent CREATE TABLE/INDEX loop
        # below. The dedup_key index in SCHEMA references a column that may
        # not exist on legacy DBs until the migration adds it.
        _migrate_product_campaign_runs(conn)
        for ddl in SCHEMA:
            conn.execute(ddl)
        cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(product_campaigns)")
        }
        if "test_mode_to" not in cols:
            conn.execute("ALTER TABLE product_campaigns ADD COLUMN test_mode_to TEXT")
        product_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(products)")
        }
        for col, ddl in (
            ("pitch_md", "TEXT"),
            ("selling_points", "TEXT"),
            ("variants_json", "TEXT"),
            ("default_budget_per_kol", "REAL"),
            ("default_budget_total", "REAL"),
            ("default_absolute_floor", "REAL"),
        ):
            if col not in product_cols:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col} {ddl}")
    finally:
        conn.close()


def _migrate_product_campaign_runs(conn: sqlite3.Connection) -> None:
    """Bring an existing ``product_campaign_runs`` table up to the current
    schema. Two things may need fixing on an older DB:

    1. The CHECK constraint on ``kind`` may not include ``'refine'`` —
       which silently dropped every refine registration via
       ``INSERT OR IGNORE`` (SQLite IGNORE skips CHECK violations too,
       not just UNIQUE).
    2. The ``dedup_key`` column may be missing.

    (1) requires rebuilding the table because SQLite cannot ALTER a CHECK
    constraint in place. (2) is a plain ADD COLUMN.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='product_campaign_runs'"
    ).fetchone()
    existing_sql = row["sql"] if row else ""
    if not existing_sql:
        # Fresh DB — let CREATE TABLE IF NOT EXISTS in SCHEMA build it.
        return
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(product_campaign_runs)")
    }
    needs_check_fix = "'refine'" not in existing_sql
    if needs_check_fix:
        # autocommit mode (isolation_level=None) — wrap explicitly so the
        # rebuild is atomic.
        conn.execute("BEGIN")
        try:
            conn.execute(
                "ALTER TABLE product_campaign_runs "
                "RENAME TO product_campaign_runs_old"
            )
            conn.execute(
                """CREATE TABLE product_campaign_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    env TEXT NOT NULL CHECK (env IN ('LIVE','TEST')),
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('outreach','reply','draft','resume','refine')),
                    session_id TEXT,
                    dedup_key TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    UNIQUE (run_id)
                )"""
            )
            old_cols = "campaign_id, env, run_id, kind, session_id, started_at, ended_at"
            conn.execute(
                f"INSERT INTO product_campaign_runs "
                f"({old_cols}) "
                f"SELECT {old_cols} FROM product_campaign_runs_old"
            )
            conn.execute("DROP TABLE product_campaign_runs_old")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        # Recreate indexes that were dropped with the old table.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_campaign_runs_cid "
            "ON product_campaign_runs(campaign_id, env, started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_campaign_runs_dedup "
            "ON product_campaign_runs(dedup_key, started_at DESC)"
        )
    elif "dedup_key" not in cols:
        conn.execute("ALTER TABLE product_campaign_runs ADD COLUMN dedup_key TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_campaign_runs_dedup "
            "ON product_campaign_runs(dedup_key, started_at DESC)"
        )


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency — one connection per request."""
    conn = _connect(get_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()
