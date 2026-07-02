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
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "cs_autopilot_jobs" in tables
        assert "cs_settings" in tables
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) == 3
        conn.close()


def test_migration_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / "idem.db")
        recreate_all(conn)
        recreate_all(conn)  # second run must not raise on duplicate ALTER
        cols = _column_names(conn, "cs_session")
        for name, _ in _SESSION_V3_COLUMNS:
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
            "SELECT quickcep_session_id, status, draft_html, customer_name FROM cs_session"
        ).fetchone()
        assert row[0] == "qc-1"
        assert row[1] == "draft_ready"
        assert row[2] is None
        assert row[3] is None
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
