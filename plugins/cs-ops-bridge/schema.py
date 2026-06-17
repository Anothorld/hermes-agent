"""CAL schema for Povison customer-service operations."""

from __future__ import annotations

import sqlite3
from typing import Final

SCHEMA_VERSION: Final[int] = 1

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
}

INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_cs_session_status ON cs_session(status, env)",
    "CREATE INDEX IF NOT EXISTS idx_cs_escalations_state ON cs_escalations(state, env)",
    "CREATE INDEX IF NOT EXISTS idx_cs_events_session ON cs_conversation_events(session_id, created_at)",
)


def recreate_all(conn: sqlite3.Connection) -> None:
    """Create or migrate schema to current version."""
    for ddl in TABLES.values():
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
