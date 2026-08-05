"""Tests for cs-ops-bridge schema v2→v3 migration (PR1.1)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_schema():
    spec = importlib.util.spec_from_file_location(
        "cs_ops_bridge_schema_test", _PLUGIN_ROOT / "schema.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cs_ops_bridge_schema_test"] = mod
    spec.loader.exec_module(mod)
    return mod


schema = _load_schema()
SCHEMA_VERSION = schema.SCHEMA_VERSION
_SESSION_V3_COLUMNS = schema._SESSION_V3_COLUMNS
_SESSION_V5_COLUMNS = schema._SESSION_V5_COLUMNS
recreate_all = schema.recreate_all


def _v2_schema_sql() -> str:
    """A minimal v2-era schema (cs_session without the v3 columns)."""
    return """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
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
    );
    INSERT INTO schema_meta(key, value) VALUES('schema_version', '2');
    """


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_has_v3_columns_and_tables():
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "fresh.db")
        recreate_all(conn)
        cols = _column_names(conn, "cs_session")
        for name, _ in _SESSION_V3_COLUMNS:
            assert name in cols, f"fresh cs_session missing {name}"
        for name, _ in _SESSION_V5_COLUMNS:
            assert name in cols, f"fresh cs_session missing {name}"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "cs_autopilot_jobs" in tables
        assert "cs_settings" in tables
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) == SCHEMA_VERSION
        conn.close()


def test_v2_db_migrates_to_v3():
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "v2.db")
        conn.executescript(_v2_schema_sql())
        conn.commit()
        cols_before = _column_names(conn, "cs_session")
        assert "draft_html" not in cols_before
        assert "customer_name" not in cols_before
        recreate_all(conn)
        cols_after = _column_names(conn, "cs_session")
        for name, _ in _SESSION_V3_COLUMNS:
            assert name in cols_after, f"migrated cs_session missing {name}"
        for name, _ in _SESSION_V5_COLUMNS:
            assert name in cols_after, f"migrated cs_session missing {name}"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "cs_autopilot_jobs" in tables
        assert "cs_settings" in tables
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) == SCHEMA_VERSION
        conn.close()


def test_migration_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "idem.db")
        recreate_all(conn)
        recreate_all(conn)  # second run must not raise on duplicate ALTER
        cols = _column_names(conn, "cs_session")
        for name, _ in _SESSION_V3_COLUMNS:
            assert name in cols
        for name, _ in _SESSION_V5_COLUMNS:
            assert name in cols
        conn.close()


def test_existing_v2_row_survives_migration():
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "v2row.db")
        conn.executescript(_v2_schema_sql())
        conn.execute(
            "INSERT INTO cs_session(quickcep_session_id, status, env, created_at, updated_at) "
            "VALUES('qc-1', 'draft_ready', 'LIVE', '2026-06-30T00:00:00+00:00', "
            "'2026-06-30T00:00:00+00:00')"
        )
        conn.commit()
        recreate_all(conn)
        row = conn.execute(
            "SELECT quickcep_session_id, status, draft_html, customer_name, processing_started_at "
            "FROM cs_session"
        ).fetchone()
        assert row[0] == "qc-1"
        assert row[1] == "draft_ready"
        assert row[2] is None
        assert row[3] is None
        # v5 column exists and is NULL for the pre-v5 row (no backfill — the
        # daily report falls back to created_at at query time).
        assert row[4] is None
        conn.close()


def test_v4_db_migrates_to_v5_adds_processing_started_at():
    """A v4-era DB (has sent_draft_* but not processing_started_at) migrates cleanly."""
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "v4.db")
        # Build a v4-shaped cs_session: v3 cols + v4 snapshot cols, no v5 col.
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE cs_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quickcep_session_id TEXT NOT NULL,
                chat_session_id TEXT,
                customer_email TEXT,
                last_message_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                env TEXT NOT NULL DEFAULT 'LIVE',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                customer_name TEXT, customer_company TEXT, locale TEXT,
                email_subject TEXT, last_message_preview TEXT, intention_tags TEXT,
                draft_html TEXT, draft_attachments TEXT, draft_updated_at TEXT,
                draft_source TEXT,
                sent_draft_html TEXT, sent_draft_source TEXT, sent_draft_at TEXT,
                UNIQUE (quickcep_session_id, env)
            );
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '4');
            INSERT INTO cs_session(quickcep_session_id, status, env, created_at, updated_at)
            VALUES ('qc-v4', 'operator_replied', 'LIVE', '2026-06-29T00:00:00+00:00',
                    '2026-06-29T00:00:00+00:00');
            """
        )
        conn.commit()
        assert "processing_started_at" not in _column_names(conn, "cs_session")
        recreate_all(conn)
        cols = _column_names(conn, "cs_session")
        assert "processing_started_at" in cols
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) == SCHEMA_VERSION
        # Existing row survives; v5 column is NULL (backfill is the report's job).
        row = conn.execute(
            "SELECT quickcep_session_id, processing_started_at FROM cs_session"
        ).fetchone()
        assert row[0] == "qc-v4"
        assert row[1] is None
        conn.close()


def test_env_updated_index_exists_and_serves_all_filter():
    """The "全部" list filter has no status predicate, so idx_cs_session_status
    cannot help and the planner would full-scan + temp-sort every session row
    (each carrying large draft_html / draft_attachments JSON). On the prod LIVE
    DB this turned the "switch to 全部" click into a ~30s hang.

    idx_cs_session_env_updated(env, updated_at DESC) must exist after
    recreate_all and must be picked by the planner for the no-status list query.
    """
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "idx.db")
        recreate_all(conn)
        # Seed a mix of statuses so the planner has realistic selectivity
        # stats; ANALYZE so it picks the index even on a tiny sample (without
        # stats SQLite may prefer a scan on near-empty tables, which is fine
        # in prod but masks the index in a unit test).
        statuses = (["draft_ready"] * 5 + ["operator_replied"] * 5
                    + ["pending"] * 5 + ["skipped"] * 5)
        for i, st in enumerate(statuses):
            conn.execute(
                "INSERT INTO cs_session(quickcep_session_id, status, env, created_at, updated_at) "
                "VALUES(?, ?, 'LIVE', ?, ?)",
                (f"qc-{i}", st,
                 f"2026-08-0{i%5+1}T00:00:00+00:00",
                 f"2026-08-0{i%5+1}T0{i%6}m:00+00:00"),
            )
        conn.commit()
        conn.execute("ANALYZE")
        # The index exists.
        idx_names = {
            r[1]
            for r in conn.execute("PRAGMA index_list(cs_session)").fetchall()
        }
        assert "idx_cs_session_env_updated" in idx_names
        # The "all" list query (no status predicate) uses the env+updated_at
        # index instead of a full scan + temp B-tree sort.
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM cs_session WHERE env=? ORDER BY updated_at DESC LIMIT 50 OFFSET 0",
            ("LIVE",),
        ).fetchall()
        plan_text = " ".join(str(p) for p in plan)
        assert "idx_cs_session_env_updated" in plan_text, plan
        assert "TEMP B-TREE" not in plan_text, plan
        # Regression guard: the status-filtered query must still use an index
        # (not degrade to a full SCAN) and must not need a temp sort. Either
        # the status index or the env+updated index is acceptable — the
        # planner picks based on selectivity, and both avoid the slow path.
        plan2 = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM cs_session WHERE env=? AND status=? ORDER BY updated_at DESC LIMIT 50 OFFSET 0",
            ("LIVE", "draft_ready"),
        ).fetchall()
        plan2_text = " ".join(str(p) for p in plan2)
        assert "SCAN cs_session" not in plan2_text, plan2
        assert "TEMP B-TREE" not in plan2_text, plan2
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
