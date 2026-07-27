"""Tests for cal.list_test_sessions / purge_sessions_by_ids (test-row cleanup)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_purge_test"


def _load_cal():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    for sub in ("schema", "pii_sanitize", "cal"):
        full = f"{_PKG}.{sub}"
        if full in sys.modules:
            continue
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
    return sys.modules[f"{_PKG}.cal"]


@pytest.fixture
def cal_mod(tmp_path, monkeypatch):
    cal = _load_cal()
    db = tmp_path / "cal.db"
    monkeypatch.setattr(cal, "_DB_PATH", db)
    # Let cal._connect() build the real schema via recreate_all — avoids
    # hand-maintaining a parallel minimal schema that drifts from schema.py.
    with cal._connect() as _c:
        _c.execute("SELECT 1").fetchone()
    return cal


def _insert_session(cal, *, qsid, env="LIVE", status="pending"):
    cal.enqueue_session(
        quickcep_session_id=qsid,
        message_id="m-" + qsid,
        env=env,
        chat_session_id="chat-" + qsid,
    )
    if status != "pending":
        with cal._connect() as c:
            c.execute("UPDATE cs_session SET status=? WHERE quickcep_session_id=? AND env=?", (status, qsid, env))
            c.commit()


def test_is_valid_quickcep_session_id(cal_mod):
    f = cal_mod.is_valid_quickcep_session_id
    assert f("2558235673303728129") is True
    assert f("sess-1") is False
    assert f("qs") is False
    assert f("x") is False
    assert f("") is False
    assert f(None) is False
    assert f("12345") is False  # too short


def test_list_test_sessions_finds_only_invalid(cal_mod):
    _insert_session(cal_mod, qsid="2558235673303728129", status="draft_ready")  # valid
    _insert_session(cal_mod, qsid="sess-1", status="draft_ready")              # invalid
    _insert_session(cal_mod, qsid="qs", status="awaiting_expert")               # invalid
    dirty = cal_mod.list_test_sessions(env="LIVE")
    qsids = sorted(s["quickcep_session_id"] for s in dirty)
    assert qsids == ["qs", "sess-1"]


def test_purge_dry_run_does_not_write(cal_mod, tmp_path):
    _insert_session(cal_mod, qsid="sess-1", status="draft_ready")
    _insert_session(cal_mod, qsid="2558235673303728129", status="draft_ready")
    with cal_mod._connect() as c:
        dirty_id = c.execute("SELECT id FROM cs_session WHERE quickcep_session_id='sess-1'").fetchone()[0]
    result = cal_mod.purge_sessions_by_ids(row_ids=[dirty_id], env="LIVE", dry_run=True, backup=True)
    assert result["mode"] == "dry_run"
    assert result["deleted"]["cs_session"] == 1
    # Nothing actually deleted.
    with cal_mod._connect() as c:
        n = c.execute("SELECT COUNT(*) FROM cs_session WHERE quickcep_session_id='sess-1'").fetchone()[0]
    assert n == 1
    # No backup file created on dry-run.
    assert not list(tmp_path.glob("*.bak-*"))


def test_purge_apply_deletes_session_and_children(cal_mod, tmp_path):
    _insert_session(cal_mod, qsid="sess-1", status="draft_ready")
    _insert_session(cal_mod, qsid="2558235673303728129", status="draft_ready")
    with cal_mod._connect() as c:
        dirty_id = c.execute("SELECT id FROM cs_session WHERE quickcep_session_id='sess-1'").fetchone()[0]
        valid_id = c.execute("SELECT id FROM cs_session WHERE quickcep_session_id='2558235673303728129'").fetchone()[0]
        # Add child rows for the dirty session (enqueue_session already wrote
        # one inbound_received event, so conversation_events will total 2).
        c.execute("INSERT INTO cs_conversation_events(session_id, event_type, created_at) VALUES (?, 'inbound_received', '2026-07-27')", (dirty_id,))
        c.execute("INSERT INTO cs_facts(session_id, namespace, fact_key, created_at, updated_at) VALUES (?, 'classify', 'category', '2026-07-27', '2026-07-27')", (dirty_id,))
        c.execute("INSERT INTO cs_escalations(session_id, reason, state, created_at, updated_at) VALUES (?, 'need_operator', 'resolved', '2026-07-27', '2026-07-27')", (dirty_id,))
        c.execute("INSERT INTO cs_message_dedup(dedup_key, quickcep_session_id, message_id, created_at) VALUES ('dk1', 'sess-1', 'm-sess-1', '2026-07-27')")
        # Add a child row for the valid session that must survive.
        c.execute("INSERT INTO cs_conversation_events(session_id, event_type, created_at) VALUES (?, 'inbound_received', '2026-07-27')", (valid_id,))
        c.commit()

    result = cal_mod.purge_sessions_by_ids(row_ids=[dirty_id], env="LIVE", dry_run=False, backup=True)
    assert result["mode"] == "applied"
    assert result["deleted"]["cs_session"] == 1
    assert result["deleted"]["cs_conversation_events"] == 2  # enqueue event + manual event
    assert result["deleted"]["cs_facts"] == 1
    assert result["deleted"]["cs_escalations"] == 1
    assert result["deleted"]["cs_message_dedup"] == 2  # enqueue dedup + manual dedup
    assert "backup_path" in result and Path(result["backup_path"]).exists()

    with cal_mod._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM cs_session WHERE quickcep_session_id='sess-1'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM cs_session WHERE quickcep_session_id='2558235673303728129'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM cs_conversation_events WHERE session_id=?", (valid_id,)).fetchone()[0] == 2  # enqueue + manual
        assert c.execute("SELECT COUNT(*) FROM cs_message_dedup WHERE quickcep_session_id='sess-1'").fetchone()[0] == 0


def test_purge_vault_link_decrements_blob_ref(cal_mod, tmp_path):
    _insert_session(cal_mod, qsid="sess-1", status="draft_ready")
    with cal_mod._connect() as c:
        dirty_id = c.execute("SELECT id FROM cs_session WHERE quickcep_session_id='sess-1'").fetchone()[0]
        c.execute("INSERT INTO cs_escalations(session_id, reason, state, created_at, updated_at) VALUES (?, 'need_operator', 'awaiting_answer', '2026-07-27', '2026-07-27')", (dirty_id,))
        esc_id = c.execute("SELECT id FROM cs_escalations WHERE session_id=?", (dirty_id,)).fetchone()[0]
        c.execute("INSERT INTO vault_blob(md5, stored_path, size_bytes, kind, ref_count, created_at) VALUES ('md5a', '/tmp/x', 1, 'image', 2, '2026-07-27')")
        c.execute("INSERT INTO escalation_vault_link(id, escalation_id, blob_md5, original_name, uploaded_at) VALUES ('lk1', ?, 'md5a', 'a.png', '2026-07-27')", (esc_id,))
        c.commit()

    result = cal_mod.purge_sessions_by_ids(row_ids=[dirty_id], env="LIVE", dry_run=False, backup=True)
    assert result["deleted"]["escalation_vault_link"] == 1
    with cal_mod._connect() as c:
        assert c.execute("SELECT ref_count FROM vault_blob WHERE md5='md5a'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM escalation_vault_link").fetchone()[0] == 0


def test_purge_empty_ids_is_noop(cal_mod):
    result = cal_mod.purge_sessions_by_ids(row_ids=[], env="LIVE", dry_run=False, backup=True)
    assert result["row_ids"] == []
    assert result["deleted"] == {}
