"""CAL schema for Povison customer-service operations."""

from __future__ import annotations

import sqlite3
from typing import Final

SCHEMA_VERSION: Final[int] = 4

SESSION_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "processing",
    "awaiting_expert",
    "draft_ready",
    "operator_replied",
    "reviewed",
    "failed",
    "skipped",
)

ESCALATION_STATES: Final[tuple[str, ...]] = (
    "open",
    "awaiting_answer",
    "answered",
    "resuming",
    "resolved",
    "re_escalated",
    "aborted",
)

TABLES: dict[str, str] = {
    "schema_meta": """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    "cs_session": """
        CREATE TABLE IF NOT EXISTS cs_session (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            quickcep_session_id   TEXT NOT NULL,
            chat_session_id       TEXT,
            customer_email        TEXT,
            last_message_id       TEXT,
            status                TEXT NOT NULL DEFAULT 'pending',
            env                   TEXT NOT NULL DEFAULT 'LIVE',
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            customer_name         TEXT,
            customer_company      TEXT,
            locale                TEXT,
            email_subject         TEXT,
            last_message_preview  TEXT,
            intention_tags        TEXT,
            draft_html            TEXT,
            draft_attachments     TEXT,
            draft_updated_at      TEXT,
            draft_source          TEXT,
            sent_draft_html       TEXT,
            sent_draft_source     TEXT,
            sent_draft_at         TEXT,
            UNIQUE (quickcep_session_id, env)
        )
    """,
    "cs_conversation_events": """
        CREATE TABLE IF NOT EXISTS cs_conversation_events (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            INTEGER NOT NULL REFERENCES cs_session(id) ON DELETE CASCADE,
            event_type            TEXT NOT NULL,
            payload_json          TEXT NOT NULL DEFAULT '{}',
            env                   TEXT NOT NULL DEFAULT 'LIVE',
            created_at            TEXT NOT NULL
        )
    """,
    "cs_facts": """
        CREATE TABLE IF NOT EXISTS cs_facts (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            INTEGER NOT NULL REFERENCES cs_session(id) ON DELETE CASCADE,
            namespace             TEXT NOT NULL,
            fact_key              TEXT NOT NULL,
            fact_value_json       TEXT NOT NULL DEFAULT 'null',
            env                   TEXT NOT NULL DEFAULT 'LIVE',
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            UNIQUE (session_id, namespace, fact_key, env)
        )
    """,
    "cs_escalations": """
        CREATE TABLE IF NOT EXISTS cs_escalations (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            INTEGER NOT NULL REFERENCES cs_session(id) ON DELETE CASCADE,
            reason                TEXT NOT NULL,
            urgency               TEXT NOT NULL DEFAULT 'medium',
            state                 TEXT NOT NULL DEFAULT 'awaiting_answer',
            question_to_operator  TEXT,
            operator_answer       TEXT,
            feishu_chat_id        TEXT,
            feishu_thread_id      TEXT,
            feishu_message_id     TEXT,
            resume_context_json   TEXT NOT NULL DEFAULT '{}',
            decision              TEXT,
            decided_by            TEXT,
            decided_at            TEXT,
            env                   TEXT NOT NULL DEFAULT 'LIVE',
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL
        )
    """,
    "cs_poller_state": """
        CREATE TABLE IF NOT EXISTS cs_poller_state (
            poller_name           TEXT PRIMARY KEY,
            state_json            TEXT NOT NULL DEFAULT '{}',
            updated_at            TEXT NOT NULL
        )
    """,
    "cs_message_dedup": """
        CREATE TABLE IF NOT EXISTS cs_message_dedup (
            dedup_key             TEXT PRIMARY KEY,
            quickcep_session_id   TEXT NOT NULL,
            message_id            TEXT NOT NULL,
            env                   TEXT NOT NULL DEFAULT 'LIVE',
            created_at            TEXT NOT NULL
        )
    """,
    "vault_blob": """
        CREATE TABLE IF NOT EXISTS vault_blob (
            md5                   TEXT PRIMARY KEY,
            stored_path           TEXT NOT NULL,
            size_bytes            INTEGER NOT NULL,
            content_type          TEXT,
            kind                  TEXT NOT NULL,
            ref_count             INTEGER NOT NULL DEFAULT 0,
            cdn_url               TEXT,
            cdn_uploaded_at       TEXT,
            created_at            TEXT NOT NULL
        )
    """,
    "escalation_vault_link": """
        CREATE TABLE IF NOT EXISTS escalation_vault_link (
            id                    TEXT PRIMARY KEY,
            escalation_id         INTEGER NOT NULL REFERENCES cs_escalations(id) ON DELETE CASCADE,
            blob_md5              TEXT NOT NULL REFERENCES vault_blob(md5),
            original_name         TEXT NOT NULL,
            uploaded_at           TEXT NOT NULL,
            uploaded_by           TEXT
        )
    """,
    "cs_autopilot_jobs": """
        CREATE TABLE IF NOT EXISTS cs_autopilot_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL REFERENCES cs_session(id) ON DELETE CASCADE,
            env             TEXT NOT NULL DEFAULT 'LIVE',
            baseline_hash   TEXT NOT NULL,
            send_at         TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'scheduled',
            claimed_at      TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            UNIQUE (session_id, env)
        )
    """,
    "cs_settings": """
        CREATE TABLE IF NOT EXISTS cs_settings (
            key         TEXT PRIMARY KEY,
            value_json  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            updated_by  TEXT
        )
    """,
}

INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_cs_session_status ON cs_session(status, env)",
    "CREATE INDEX IF NOT EXISTS idx_cs_escalations_state ON cs_escalations(state, env)",
    "CREATE INDEX IF NOT EXISTS idx_cs_events_session ON cs_conversation_events(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_vault_link_esc ON escalation_vault_link(escalation_id)",
    "CREATE INDEX IF NOT EXISTS idx_vault_link_blob ON escalation_vault_link(blob_md5)",
    "CREATE INDEX IF NOT EXISTS idx_ap_send_at ON cs_autopilot_jobs(send_at) WHERE status='scheduled'",
)

# Columns added to cs_session by v2→v3 migration (PR1.1). Fresh DBs get them via
# the CREATE TABLE definition above; existing v2 DBs get them via ALTER TABLE.
_SESSION_V3_COLUMNS: tuple[tuple[str, str], ...] = (
    ("customer_name", "TEXT"),
    ("customer_company", "TEXT"),
    ("locale", "TEXT"),
    ("email_subject", "TEXT"),
    ("last_message_preview", "TEXT"),
    ("intention_tags", "TEXT"),
    ("draft_html", "TEXT"),
    ("draft_attachments", "TEXT"),
    ("draft_updated_at", "TEXT"),
    ("draft_source", "TEXT"),
)

# Columns added to cs_session by v3→v4 migration (draft snapshot for adoption tracking).
# Fresh DBs get them via the CREATE TABLE definition above; existing v3 DBs get them
# via ALTER TABLE.
_SESSION_V4_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sent_draft_html", "TEXT"),
    ("sent_draft_source", "TEXT"),
    ("sent_draft_at", "TEXT"),
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add cs_session columns introduced in schema v3 (idempotent per column)."""
    existing = _existing_columns(conn, "cs_session")
    for name, coltype in _SESSION_V3_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE cs_session ADD COLUMN {name} {coltype}")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Add cs_session columns for draft snapshot (idempotent per column)."""
    existing = _existing_columns(conn, "cs_session")
    for name, coltype in _SESSION_V4_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE cs_session ADD COLUMN {name} {coltype}")


def migrate(conn: sqlite3.Connection) -> None:
    """Run forward migrations based on the persisted schema_version."""
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    # schema_meta may not exist yet on a truly fresh connection before recreate_all
    # has run; CREATE TABLE IF NOT EXISTS above handles that. If no row, treat as 1.
    try:
        current = int(row[0]) if row else 1
    except (TypeError, ValueError):
        current = 1
    if current < 3:
        _migrate_v2_to_v3(conn)
    if current < 4:
        _migrate_v3_to_v4(conn)


def recreate_all(conn: sqlite3.Connection) -> None:
    """Create or migrate schema to current version."""
    for ddl in TABLES.values():
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    migrate(conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
