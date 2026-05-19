"""CAL schema definitions.

Single source of truth for the Conversation Audit Layer DDL.
Kept in its own module so tests and reconcile tooling can import the
exact same `CREATE TABLE` strings without round-tripping through the
runtime DB connection.

Design notes:
- All tables carry `env` (`TEST` | `LIVE`) so test data can be wiped
  in bulk without dropping production rows.
- `kol_identity` is the global KOL entity. Cards in Hermes Kanban are
  per-product/per-campaign instances pointing back to one identity via
  `kol_identity_id`.
- `kol_identity_alias` is the lookup index used by the reply dispatcher
  to re-link inbound mail when threadId/email/handle change. The
  composite UNIQUE constraint prevents the same alias being claimed
  by two identities.
- `kol_draft_history.context_snapshot_json` is the single answer to
  "why was this email generated like this?" It is the only acceptable
  data source for the Web "generation rationale" panel.
- `kol_negotiation_history` is append-only; the Kanban card's
  `negotiation.last_*` fields are derived pointers, not the truth.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# Ordered so foreign keys resolve cleanly on init.
TABLES: dict[str, str] = {
    "schema_meta": """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    "kol_identity": """
        CREATE TABLE IF NOT EXISTS kol_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'instagram',
            primary_email TEXT,
            display_name TEXT,
            region TEXT,
            creator_type TEXT,
            blacklisted INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            env TEXT NOT NULL DEFAULT 'LIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (platform, handle, env)
        )
    """,
    "kol_identity_alias": """
        CREATE TABLE IF NOT EXISTS kol_identity_alias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_identity_id INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,            -- gmail_thread_id | gmail_message_id | email | handle
            value TEXT NOT NULL,
            source TEXT NOT NULL,          -- discovery | dispatcher | manual_web | reconcile
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            env TEXT NOT NULL DEFAULT 'LIVE',
            UNIQUE (kind, value, env)
        )
    """,
    "kol_conversation_events": """
        CREATE TABLE IF NOT EXISTS kol_conversation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_identity_id INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            card_id TEXT,                  -- Kanban task id, nullable for pre-card events
            product_sku TEXT,
            campaign_id TEXT,
            event_type TEXT NOT NULL,      -- discovered | approved | emailed_<stage> | reply_received | classified_<intent> | stage_changed | escalated | contract_<sub> | logistics_<sub> | content_<sub> | closed
            stage TEXT,                    -- 8-stage value at event time
            sub_status TEXT,
            actor TEXT NOT NULL,           -- chat | web | cron | dispatcher | discovery | manual:<user>
            ts TEXT NOT NULL,
            payload_json TEXT,             -- arbitrary structured payload
            env TEXT NOT NULL DEFAULT 'LIVE'
        )
    """,
    "kol_draft_history": """
        CREATE TABLE IF NOT EXISTS kol_draft_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_identity_id INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            card_id TEXT,
            campaign_id TEXT,
            product_sku TEXT,
            stage TEXT NOT NULL,           -- initial | product_pitch | negotiation | contract | logistics | content_revision
            sub_status TEXT,
            draft_id TEXT NOT NULL,        -- Gmail draft id
            gmail_message_id TEXT,
            gmail_thread_id TEXT,
            subject TEXT,
            body TEXT,                     -- full body, no PII redaction (internal use)
            body_hash TEXT,
            context_snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_at TEXT,                  -- populated by reconcile when label moves
            actor TEXT NOT NULL,
            triggered_by TEXT NOT NULL,    -- chat | web | cron
            env TEXT NOT NULL DEFAULT 'LIVE',
            UNIQUE (draft_id, env)
        )
    """,
    "kol_reply_history": """
        CREATE TABLE IF NOT EXISTS kol_reply_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_identity_id INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            card_id TEXT,
            campaign_id TEXT,
            gmail_message_id TEXT NOT NULL,
            gmail_thread_id TEXT,
            from_addr TEXT,
            received_at TEXT NOT NULL,
            snippet TEXT,
            body TEXT,
            intent TEXT,                   -- positive | negative | price_request | content_submission | question | unknown
            confidence REAL,
            match_strategy TEXT NOT NULL,  -- thread_id | in_reply_to | from_addr | handle_mention | unmatched
            match_confidence REAL NOT NULL,
            handled_action TEXT,           -- routed_skill | escalated | ignored
            env TEXT NOT NULL DEFAULT 'LIVE',
            UNIQUE (gmail_message_id, env)
        )
    """,
    "kol_negotiation_history": """
        CREATE TABLE IF NOT EXISTS kol_negotiation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_identity_id INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            card_id TEXT,
            campaign_id TEXT,
            product_sku TEXT,
            seq INTEGER NOT NULL,          -- 1-based round number
            kol_request_amount REAL,
            currency TEXT NOT NULL DEFAULT 'USD',
            agent_counter_amount REAL,
            decision TEXT NOT NULL,        -- accept | counter | refuse_escalate | parse_failed
            decision_reason TEXT,
            budget_per_kol_at_time REAL,
            absolute_floor_at_time REAL,
            decided_at TEXT NOT NULL,
            human_decision TEXT,           -- accept | modify | reject | (null)
            human_decided_at TEXT,
            human_note TEXT,
            env TEXT NOT NULL DEFAULT 'LIVE'
        )
    """,
    "escalation_history": """
        CREATE TABLE IF NOT EXISTS escalation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_identity_id INTEGER REFERENCES kol_identity(id) ON DELETE CASCADE,
            card_id TEXT,
            campaign_id TEXT,
            ts TEXT NOT NULL,
            reason TEXT NOT NULL,          -- low_confidence_reply | floor_violation | unknown_sender | parse_failed | sku_off_whitelist | missing_email | content_revision_overflow | manual
            classifier_confidence REAL,
            ai_recommendation TEXT,
            human_decision TEXT,
            human_decided_at TEXT,
            human_note TEXT,
            env TEXT NOT NULL DEFAULT 'LIVE'
        )
    """,
}

# Indexes for the read patterns that actually matter
# (timeline lookup, alias resolution, recent-events feed).
INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_events_kol_ts        ON kol_conversation_events (kol_identity_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_events_campaign_ts   ON kol_conversation_events (campaign_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_events_product_ts    ON kol_conversation_events (product_sku, ts)",
    "CREATE INDEX IF NOT EXISTS ix_events_type_ts       ON kol_conversation_events (event_type, ts)",
    "CREATE INDEX IF NOT EXISTS ix_drafts_kol_created   ON kol_draft_history (kol_identity_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_drafts_card          ON kol_draft_history (card_id)",
    "CREATE INDEX IF NOT EXISTS ix_replies_kol_received ON kol_reply_history (kol_identity_id, received_at)",
    "CREATE INDEX IF NOT EXISTS ix_replies_thread       ON kol_reply_history (gmail_thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_negot_kol_seq        ON kol_negotiation_history (kol_identity_id, seq)",
    "CREATE INDEX IF NOT EXISTS ix_alias_lookup         ON kol_identity_alias (kind, value)",
    "CREATE INDEX IF NOT EXISTS ix_escalation_kol_ts    ON escalation_history (kol_identity_id, ts)",
]
