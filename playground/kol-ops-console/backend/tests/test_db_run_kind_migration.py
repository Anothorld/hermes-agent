"""product_campaign_runs kind CHECK includes creator_brief_refresh."""

from __future__ import annotations

import sqlite3

from app.db import _migrate_product_campaign_runs


def test_migrate_adds_creator_brief_refresh_kind() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute(
        """CREATE TABLE product_campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL CHECK (env IN ('LIVE','TEST')),
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('outreach','reply','draft','resume','refine','email_discover')),
            session_id TEXT,
            dedup_key TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            UNIQUE (run_id)
        )"""
    )
    _migrate_product_campaign_runs(conn)
    conn.execute(
        """INSERT INTO product_campaign_runs
            (campaign_id, env, run_id, kind, session_id, started_at)
           VALUES ('C1', 'LIVE', 'run-brief-1', 'creator_brief_refresh',
                   'kol-creator-brief-refresh:LIVE:9:tok', '2026-06-01T00:00:00+00:00')"""
    )
    row = conn.execute(
        "SELECT kind FROM product_campaign_runs WHERE run_id='run-brief-1'",
    ).fetchone()
    assert row is not None
    assert row["kind"] == "creator_brief_refresh"
