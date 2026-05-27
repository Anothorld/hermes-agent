"""CAL schema v2 — goal-driven KOL pipeline.

Three-tier memory: identity (kol_identity, kol_relationship) | campaign
(campaign_config, campaign_candidates, kol_goal_state) | thread/event
(kol_facts, kol_conversation_events, kol_escalations); plus
policy_documents. `kol_facts.fact_key` is namespace-prefixed via CHECK.
``recreate_all(conn)`` is the single migration entry point (hard-cut).
"""

from __future__ import annotations

import sqlite3
from typing import Final

SCHEMA_VERSION: Final[int] = 3

FACT_NAMESPACES: Final[tuple[str, ...]] = ("identity", "offer", "fulfillment", "approval", "payout")

GOAL_NAMES: Final[tuple[str, ...]] = (
    "outreach",
    "interest_qualification",
    "product_selection",
    "deliverables_scope",
    "compensation_negotiation",
    "contract_signing",
    "logistics",
    "payout_setup",
    "content_production",
    "content_review_and_golive",
    "post_collab_archival",
)

LANES: Final[tuple[str, ...]] = ("commerce", "fulfillment", "publish", "meta")


# Ordered so foreign keys resolve cleanly on init.
TABLES: dict[str, str] = {
    "schema_meta": """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    "kol_identity": """
        CREATE TABLE IF NOT EXISTS kol_identity (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_handle            TEXT NOT NULL,
            platform                  TEXT NOT NULL DEFAULT 'instagram',
            primary_email             TEXT,
            alt_handles_json          TEXT NOT NULL DEFAULT '[]',
            contact_role              TEXT NOT NULL DEFAULT 'kol',  -- kol|manager|agency
            display_name              TEXT,
            region                    TEXT,
            language                  TEXT,
            default_shipping_address  TEXT,                          -- JSON object
            default_payment_method    TEXT,
            blacklisted               INTEGER NOT NULL DEFAULT 0,
            notes                     TEXT,
            env                       TEXT NOT NULL DEFAULT 'LIVE',
            created_at                TEXT NOT NULL,
            updated_at                TEXT NOT NULL,
            UNIQUE (platform, primary_handle, env)
        )
    """,
    "kol_relationship": """
        CREATE TABLE IF NOT EXISTS kol_relationship (
            identity_id           INTEGER PRIMARY KEY REFERENCES kol_identity(id) ON DELETE CASCADE,
            total_collabs         INTEGER NOT NULL DEFAULT 0,
            last_campaign_id      TEXT,
            last_outcome          TEXT,                              -- success|disputed|content_failed|aborted|...
            reputation_score      REAL,
            preferred_skus_json   TEXT NOT NULL DEFAULT '[]',
            preferred_mode        TEXT NOT NULL DEFAULT 'unknown',   -- gifted|paid|commission|hybrid|unknown
            avg_delivery_quality  REAL,
            avg_revision_rounds   REAL,
            last_archived_at      TEXT,
            updated_at            TEXT NOT NULL
        )
    """,
    "campaign_config": """
        CREATE TABLE IF NOT EXISTS campaign_config (
            campaign_id                       TEXT PRIMARY KEY,
            label                             TEXT,
            product_display_name              TEXT,
            product_url                       TEXT,
            product_unit_price                REAL,
            barter_policy                     TEXT,
            paid_ceiling                      REAL,
            commission_band_json              TEXT NOT NULL DEFAULT '{}',
            deliverable_platforms_json        TEXT NOT NULL DEFAULT '[]',
            deliverable_count_per_platform    INTEGER,
            extra_notes                       TEXT,
            brief_template_id                 TEXT,
            sku_whitelist_json                TEXT NOT NULL DEFAULT '[]',
            color_variant_policy              TEXT,
            audit_standards_md                TEXT,
            test_mode_to                      TEXT,
            followup_intervals_json           TEXT NOT NULL DEFAULT '{}',
            contract_required                 INTEGER NOT NULL DEFAULT 1,
            status                            TEXT NOT NULL DEFAULT 'draft',  -- draft|approved|active|closed
            env                               TEXT NOT NULL DEFAULT 'LIVE',
            created_at                        TEXT NOT NULL,
            updated_at                        TEXT NOT NULL
        )
    """,
    "campaign_candidates": """
        CREATE TABLE IF NOT EXISTS campaign_candidates (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id         TEXT NOT NULL REFERENCES campaign_config(campaign_id) ON DELETE CASCADE,
            identity_id         INTEGER REFERENCES kol_identity(id) ON DELETE CASCADE,
            source              TEXT NOT NULL,                       -- discovery|manual_web|reengagement_picker
            discovery_score     REAL,
            relationship_status TEXT NOT NULL DEFAULT 'new_prospect',-- new_prospect|repeat_kol|repeat_kol_needs_review|rejected
            candidate_status    TEXT NOT NULL DEFAULT 'discovered',  -- discovered|shortlisted|selected_for_outreach|needs_review|rejected
            review_reason       TEXT,
            selected_by         TEXT,
            selected_at         TEXT,
            payload_json        TEXT NOT NULL DEFAULT '{}',
            env                 TEXT NOT NULL DEFAULT 'LIVE',
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            UNIQUE (campaign_id, identity_id, env)
        )
    """,
    "kol_goal_state": """
        CREATE TABLE IF NOT EXISTS kol_goal_state (
            identity_id           INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            campaign_id           TEXT NOT NULL,
            goal                  TEXT NOT NULL,
            status                TEXT NOT NULL DEFAULT 'inactive', -- inactive|active|satisfied|blocked|skipped|aborted
            lane                  TEXT NOT NULL,                    -- commerce|fulfillment|publish|meta
            missing_facts_json    TEXT NOT NULL DEFAULT '[]',
            meta_json             TEXT NOT NULL DEFAULT '{}',
            blocking_escalation_id INTEGER,
            last_event_id         INTEGER,
            updated_at            TEXT NOT NULL,
            env                   TEXT NOT NULL DEFAULT 'LIVE',
            PRIMARY KEY (identity_id, campaign_id, goal, env)
        )
    """,
    "kol_facts": """
        CREATE TABLE IF NOT EXISTS kol_facts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_id     INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            campaign_id     TEXT,
            fact_namespace  TEXT NOT NULL CHECK (fact_namespace IN ('identity','offer','fulfillment','approval','payout')),
            fact_key        TEXT NOT NULL,
            fact_value      TEXT,                                   -- JSON-encoded scalar/object
            source          TEXT NOT NULL,                          -- email|skill|manual|seed
            source_event_id INTEGER,
            captured_at     TEXT NOT NULL,
            env             TEXT NOT NULL DEFAULT 'LIVE',
            CHECK (fact_key LIKE fact_namespace || '.%')
        )
    """,
    "kol_conversation_events": """
        CREATE TABLE IF NOT EXISTS kol_conversation_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_id     INTEGER NOT NULL REFERENCES kol_identity(id) ON DELETE CASCADE,
            campaign_id     TEXT,
            event_type      TEXT NOT NULL,
            goal            TEXT,
            lane            TEXT,
            actor           TEXT NOT NULL,                          -- chat|web|cron|dispatcher|skill|manual:<user>
            ts              TEXT NOT NULL,
            payload_json    TEXT,
            env             TEXT NOT NULL DEFAULT 'LIVE'
        )
    """,
    "kol_escalations": """
        CREATE TABLE IF NOT EXISTS kol_escalations (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_id             INTEGER REFERENCES kol_identity(id) ON DELETE CASCADE,
            campaign_id             TEXT,
            goal                    TEXT,
            reason                  TEXT NOT NULL,
            severity                TEXT NOT NULL DEFAULT 'normal',  -- normal|critical|blocking
            state                   TEXT NOT NULL DEFAULT 'awaiting_answer',
            -- state ∈ open|awaiting_answer|answered|resuming|resolved|re_escalated|aborted
            question_to_operator    TEXT,
            operator_answer         TEXT,
            operator_facts_json     TEXT,
            parent_escalation_id    INTEGER REFERENCES kol_escalations(id) ON DELETE SET NULL,
            attempts_count          INTEGER NOT NULL DEFAULT 1,
            resume_context_json     TEXT,
            decision                TEXT,                            -- inject_and_continue|override_and_continue|escalate_again|abort
            decided_by              TEXT,
            decided_at              TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            env                     TEXT NOT NULL DEFAULT 'LIVE'
        )
    """,
    "policy_documents": """
        CREATE TABLE IF NOT EXISTS policy_documents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scope           TEXT NOT NULL,                            -- company_style|user_style|escalation_rules
            owner_user_id   INTEGER,
            title           TEXT,
            content_md      TEXT NOT NULL DEFAULT '',
            version         INTEGER NOT NULL DEFAULT 1,
            updated_by      TEXT,
            updated_at      TEXT NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1
        )
    """,
}


VIEWS: dict[str, str] = {
    "kol_facts_latest": """
        CREATE VIEW IF NOT EXISTS kol_facts_latest AS
        SELECT f.*
          FROM kol_facts f
          JOIN (
                SELECT identity_id, campaign_id, fact_key, env, MAX(id) AS max_id
                  FROM kol_facts
                 GROUP BY identity_id, campaign_id, fact_key, env
               ) m
            ON m.max_id = f.id
    """,
}


INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_facts_id_camp_ns       ON kol_facts (identity_id, campaign_id, fact_namespace)",
    "CREATE INDEX IF NOT EXISTS ix_facts_key              ON kol_facts (fact_key)",
    "CREATE INDEX IF NOT EXISTS ix_events_id_ts           ON kol_conversation_events (identity_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_events_camp_ts         ON kol_conversation_events (campaign_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_goal_state_camp        ON kol_goal_state (campaign_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_goal_state_lane        ON kol_goal_state (lane, status)",
    "CREATE INDEX IF NOT EXISTS ix_candidates_camp_status ON campaign_candidates (campaign_id, candidate_status)",
    "CREATE INDEX IF NOT EXISTS ix_escal_state            ON kol_escalations (state, env)",
    "CREATE INDEX IF NOT EXISTS ix_escal_id_state         ON kol_escalations (identity_id, state)",
    "CREATE INDEX IF NOT EXISTS ix_policy_scope_active    ON policy_documents (scope, is_active)",
]


def recreate_all(conn: sqlite3.Connection) -> None:
    """Hard-cut rebuild: drop every CAL object then re-create from DDLs.

    Intended for tests, demo seeding, and the one-shot v2 migration.
    Caller must commit. Does not preserve data.
    """
    # Drop in reverse FK order. Use IF EXISTS so partial states are safe.
    for name in reversed(list(TABLES)):
        conn.execute(f"DROP TABLE IF EXISTS {name}")
    for name in VIEWS:
        conn.execute(f"DROP VIEW IF EXISTS {name}")
    for ddl in TABLES.values():
        conn.execute(ddl)
    for ddl in VIEWS.values():
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
        (str(SCHEMA_VERSION),),
    )
