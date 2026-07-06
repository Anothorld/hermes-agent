"""Tests for the daily-report correctness fixes (schema v5 + aggregate API).

Covers the three bugs the daily-fix PR addresses:
  1. ``processing_started_at`` is stamped once when a session leaves ``pending``
     and never overwritten by later transitions (so the daily report can bucket
     by first-active day instead of the volatile ``updated_at``).
  2. ``escalations_in_window`` counts escalations by ``created_at`` in the
     window, not by snapshot ``status == 'awaiting_expert'`` — escalations
     answered the same day are no longer dropped.
  3. ``draft_saved_session_ids`` returns the CAL event set so the daily report
     can count AI drafts even when ``draft_source`` was never stamped.
  4. ``list_sessions(since, until)`` filters server-side on
     ``COALESCE(processing_started_at, created_at)`` so the report no longer
     truncates at a 50-row client-side page.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_daily_fix_test"


def _load_pkg_module(sub: str):
    if _PKG not in sys.modules:
        import types

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


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    db = tmp_path / "cal_daily.db"
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(db))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


def _enqueue(cal, sid, *, env="LIVE", message_id=None):
    return cal.enqueue_session(
        quickcep_session_id=sid,
        customer_email=f"{sid}@example.com",
        message_id=message_id or f"m-{sid}",
        env=env,
    )


def test_processing_started_at_stamped_once_and_not_overwritten(cal):
    """First transition out of pending stamps; later transitions preserve it."""
    res = _enqueue(cal, "qc-stamp")
    sess = cal.get_session(quickcep_session_id="qc-stamp")
    assert sess["status"] == "pending"
    assert sess["processing_started_at"] is None

    # pending -> processing: stamps processing_started_at.
    cal.update_session_status(session_row_id=sess["id"], status="processing")
    t1 = cal.get_session(quickcep_session_id="qc-stamp")["processing_started_at"]
    assert t1 is not None

    # processing -> draft_ready: must NOT overwrite the original stamp.
    cal.update_session_status(session_row_id=sess["id"], status="draft_ready")
    t2 = cal.get_session(quickcep_session_id="qc-stamp")["processing_started_at"]
    assert t2 == t1

    # draft_ready -> operator_replied: still preserved.
    cal.update_session_status(session_row_id=sess["id"], status="operator_replied")
    t3 = cal.get_session(quickcep_session_id="qc-stamp")["processing_started_at"]
    assert t3 == t1


def test_escalations_in_window_counts_by_created_at_not_snapshot(cal):
    """An escalation answered the same day still counts for that day."""
    res = _enqueue(cal, "qc-esc")
    sess = cal.get_session(quickcep_session_id="qc-esc")
    # Move into active processing first so created_at lines up with the window.
    cal.update_session_status(session_row_id=sess["id"], status="processing")
    started = cal.get_session(quickcep_session_id="qc-esc")["processing_started_at"]

    # Open an escalation row whose created_at falls inside the window.
    cal.write_event(
        quickcep_session_id="qc-esc",
        event_type="session_handoff",
        payload={"phase": "awaiting_expert"},
    )
    # Insert an escalation directly via the cal helper if available; otherwise
    # use the open_escalation path. We use the public write path through the
    # escalations module if importable, else fall back to a direct insert.
    try:
        esc_mod = _load_pkg_module("escalations")
        esc_mod.open_escalation(
            quickcep_session_id="qc-esc",
            reason="test",
            env="LIVE",
        )
    except Exception:
        with cal._connect() as conn:  # noqa: SLF001 — test-only backdoor
            conn.execute(
                "INSERT INTO cs_escalations(session_id, reason, state, env, created_at, updated_at) "
                "VALUES (?, 'test', 'awaiting_answer', 'LIVE', ?, ?)",
                (sess["id"], started, started),
            )
            conn.commit()

    window_since = started
    # Build an exclusive upper bound strictly after started.
    from datetime import datetime, timezone, timedelta
    upper = (datetime.fromisoformat(started) + timedelta(hours=1)).isoformat()

    out = cal.escalations_in_window(env="LIVE", since=window_since, until=upper)
    assert out["count"] == 1
    assert out["items"][0]["quickcep_session_id"] == "qc-esc"


def test_draft_saved_session_ids_returns_event_set(cal):
    """Sessions with a draft_saved event are returned; sessions without are not."""
    _enqueue(cal, "qc-with-draft")
    _enqueue(cal, "qc-no-draft")
    s_with = cal.get_session(quickcep_session_id="qc-with-draft")
    s_without = cal.get_session(quickcep_session_id="qc-no-draft")

    cal.save_draft(
        quickcep_session_id="qc-with-draft",
        draft_html="<p>hello world this is a long enough draft</p>",
        source="agent",
    )

    # Use a wide window that definitely contains the event timestamps.
    out = cal.draft_saved_session_ids(env="LIVE", since="2000-01-01T00:00:00Z", until="2999-01-01T00:00:00Z")
    assert s_with["id"] in out
    assert s_without["id"] not in out


def test_list_sessions_since_until_filters_on_first_active_time(cal):
    """Server-side window filter uses processing_started_at (or created_at fallback)."""
    # qc-old: stays pending → no processing_started_at → falls back to created_at.
    # Insert with an explicit far-past created_at so it falls outside the window
    # we'll build around qc-today's first-active stamp.
    _enqueue(cal, "qc-old")
    s_old = cal.get_session(quickcep_session_id="qc-old")
    with cal._connect() as conn:  # noqa: SLF001 — test-only backdoor
        conn.execute(
            "UPDATE cs_session SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", s_old["id"]),
        )
        conn.commit()

    # qc-today: leaves pending → processing_started_at stamped "now".
    _enqueue(cal, "qc-today")
    s_today = cal.get_session(quickcep_session_id="qc-today")
    cal.update_session_status(session_row_id=s_today["id"], status="processing")
    started = cal.get_session(quickcep_session_id="qc-today")["processing_started_at"]

    from datetime import datetime, timezone, timedelta
    upper = (datetime.fromisoformat(started) + timedelta(hours=1)).isoformat()
    lower = (datetime.fromisoformat(started) - timedelta(seconds=1)).isoformat()

    rows = cal.list_sessions(env="LIVE", since=lower, until=upper, limit=200)
    ids = {r["quickcep_session_id"] for r in rows}
    assert "qc-today" in ids
    assert "qc-old" not in ids


def test_daily_report_stats_bundles_all_three_reads(cal):
    """The one-shot aggregate returns sessions + escalations + draft_saved ids."""
    _enqueue(cal, "qc-bundle")
    s = cal.get_session(quickcep_session_id="qc-bundle")
    cal.update_session_status(session_row_id=s["id"], status="processing")
    cal.save_draft(
        quickcep_session_id="qc-bundle",
        draft_html="<p>bundle draft long enough for the threshold check</p>",
        source="agent",
    )
    started = cal.get_session(quickcep_session_id="qc-bundle")["processing_started_at"]
    from datetime import datetime, timezone, timedelta
    lower = (datetime.fromisoformat(started) - timedelta(seconds=1)).isoformat()
    upper = (datetime.fromisoformat(started) + timedelta(hours=1)).isoformat()

    out = cal.daily_report_stats(env="LIVE", since=lower, until=upper)
    assert set(out.keys()) >= {"sessions", "escalations", "draft_saved_session_ids", "since", "until"}
    assert any(r["quickcep_session_id"] == "qc-bundle" for r in out["sessions"])
    assert s["id"] in set(out["draft_saved_session_ids"])


def test_daily_report_stats_excludes_skipped_sessions(cal, monkeypatch):
    """PR3 fix: permanent-skip rows (status=skipped) must not inflate the daily report's processed count."""
    _enqueue(cal, "qc-real")
    s_real = cal.get_session(quickcep_session_id="qc-real")
    cal.update_session_status(session_row_id=s_real["id"], status="processing")

    _enqueue(cal, "qc-skipped")
    s_skip = cal.get_session(quickcep_session_id="qc-skipped")
    cal.update_session_status(session_row_id=s_skip["id"], status="skipped")

    started = cal.get_session(quickcep_session_id="qc-real")["processing_started_at"]
    from datetime import datetime, timezone, timedelta
    lower = (datetime.fromisoformat(started) - timedelta(seconds=1)).isoformat()
    upper = (datetime.fromisoformat(started) + timedelta(hours=1)).isoformat()

    out = cal.daily_report_stats(env="LIVE", since=lower, until=upper)
    qsids = {r["quickcep_session_id"] for r in out["sessions"]}
    assert "qc-real" in qsids
    assert "qc-skipped" not in qsids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
