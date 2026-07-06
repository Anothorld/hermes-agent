"""PR2 tests: schema v6 agent_processing_at column + stamp logic."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr2_agent_proc_test"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if key == _PKG or key.startswith(f"{_PKG}."):
            del sys.modules[key]


def _load(sub: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


def test_fresh_db_has_agent_processing_at_column(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r = cal.enqueue_session(quickcep_session_id="qs-fresh", message_id="m1", env="LIVE")
    with cal._connect() as conn:  # noqa: SLF001
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cs_session)")}
    assert "agent_processing_at" in cols
    sess = cal.get_session(quickcep_session_id="qs-fresh", env="LIVE")
    assert sess["agent_processing_at"] is None


def test_v5_to_v6_migration_adds_column(monkeypatch, tmp_path):
    _reset_modules()
    db = tmp_path / "cal_v5.db"
    # Build a v5 schema manually (without agent_processing_at), stamp version=5.
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE cs_session (id INTEGER PRIMARY KEY, quickcep_session_id TEXT, env TEXT, "
        "status TEXT, created_at TEXT, updated_at TEXT, processing_started_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','5')")
    conn.execute(
        "INSERT INTO cs_session(id,quickcep_session_id,env,status,created_at,updated_at) "
        "VALUES(1,'qs-old','LIVE','processing','2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(db))
    cal = _load("cal")  # import triggers _connect → recreate_all → migrate
    with cal._connect() as conn:  # noqa: SLF001
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cs_session)")}
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
    assert "agent_processing_at" in cols
    assert version == "6"
    sess = cal.get_session(quickcep_session_id="qs-old", env="LIVE")
    assert sess["agent_processing_at"] is None


def test_stamp_agent_processing_at_is_idempotent(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    r = cal.enqueue_session(quickcep_session_id="qs-stamp", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    cal.stamp_agent_processing_at(session_row_id=r["session"]["id"])
    first = cal.get_session(quickcep_session_id="qs-stamp", env="LIVE")["agent_processing_at"]
    assert first is not None
    # Second stamp must NOT overwrite (COALESCE keeps earliest).
    cal.stamp_agent_processing_at(session_row_id=r["session"]["id"])
    second = cal.get_session(quickcep_session_id="qs-stamp", env="LIVE")["agent_processing_at"]
    assert second == first


def test_apply_handoff_processing_stamps_agent_processing_at(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    sh = _load("session_handoff")

    r = cal.enqueue_session(quickcep_session_id="qs-handoff", message_id="m1", env="LIVE")
    # Watcher already set processing; agent now calls apply-handoff processing.
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    assert cal.get_session(quickcep_session_id="qs-handoff", env="LIVE")["agent_processing_at"] is None

    from unittest.mock import patch
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"processing": "ai-proc"},
        "business": {},
        "inquiry_by_category": {"product": "inq-prod"},
    }), patch.object(sh, "_run_quickcep_cli", return_value={"ok": True}):
        result = sh.apply_handoff(
            quickcep_session_id="qs-handoff",
            phase="processing",
            env="LIVE",
            chat_session_id="chat-1",
            skip_quickcep=True,
        )
    assert result["ok"] is True
    sess = cal.get_session(quickcep_session_id="qs-handoff", env="LIVE")
    assert sess["agent_processing_at"] is not None


def test_regression_path_does_not_stamp(monkeypatch, tmp_path):
    """When status regression is skipped, agent_processing_at must NOT be stamped."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    sh = _load("session_handoff")

    r = cal.enqueue_session(quickcep_session_id="qs-regress", message_id="m1", env="LIVE")
    # Session already in draft_ready (rank higher than processing) → regression skip.
    cal.update_session_status(session_row_id=r["session"]["id"], status="draft_ready")
    cal.save_draft(quickcep_session_id="qs-regress", draft_html="<p>x</p>", env="LIVE")

    from unittest.mock import patch
    with patch.object(sh, "load_tag_map", return_value={
        "ai_lifecycle": {"processing": "ai-proc", "draft_ready": "ai-draft"},
        "business": {},
        "inquiry_by_category": {"product": "inq-prod"},
    }), patch.object(sh, "_run_quickcep_cli", return_value={"ok": True}):
        result = sh.apply_handoff(
            quickcep_session_id="qs-regress",
            phase="processing",
            env="LIVE",
            chat_session_id="chat-1",
            skip_quickcep=True,
        )
    # handoff may still return ok (tags/note), but status regression skipped.
    sess = cal.get_session(quickcep_session_id="qs-regress", env="LIVE")
    assert sess["agent_processing_at"] is None
