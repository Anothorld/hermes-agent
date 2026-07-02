"""Tests for PR1.3: cal.save_draft (draft persisted to CAL, not QuickCEP)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr13_test"


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
    db = tmp_path / "cal13.db"
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(db))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


def _enqueue(cal, sid="qc-300"):
    cal.enqueue_session(quickcep_session_id=sid, customer_email="a@b.com", message_id="m1")
    return cal.get_session(quickcep_session_id=sid)


def test_save_draft_persists_html_attachments_source(cal):
    _enqueue(cal)
    res = cal.save_draft(
        quickcep_session_id="qc-300",
        draft_html="<p>reply</p>",
        attachments=[{"fileName": "a.pdf", "url": "https://x/a.pdf"}],
        source="agent",
        subject="Re: order",
    )
    assert res["success"] is True
    assert res["stored"] == "cal"
    sess = cal.get_session(quickcep_session_id="qc-300")
    assert sess["draft_html"] == "<p>reply</p>"
    assert json.loads(sess["draft_attachments"]) == [{"fileName": "a.pdf", "url": "https://x/a.pdf"}]
    assert sess["draft_source"] == "agent"
    assert sess["draft_updated_at"] is not None
    assert sess["email_subject"] == "Re: order"


def test_clear_draft_empties_after_send(cal):
    """clear_draft should wipe draft_html/attachments and mark source='sent'."""
    _enqueue(cal)
    cal.save_draft(
        quickcep_session_id="qc-300",
        draft_html="<p>reply</p>",
        attachments=[{"fileName": "a.pdf", "url": "https://x/a.pdf"}],
        source="operator_edit",
    )
    sess = cal.get_session(quickcep_session_id="qc-300")
    assert sess["draft_html"] == "<p>reply</p>"
    # snapshot the edit_memory baseline into cs_facts (as send_reply would before clearing)
    cal.write_facts(quickcep_session_id="qc-300", namespaces={"edit_memory": {"ai_baseline_html": "<p>ai</p>"}})
    cal.clear_draft(quickcep_session_id="qc-300")
    sess = cal.get_session(quickcep_session_id="qc-300")
    assert sess["draft_html"] == ""
    assert json.loads(sess["draft_attachments"]) == []
    assert sess["draft_source"] == "sent"
    # cs_facts edit_memory baseline must survive clear_draft (run reads it inline anyway)
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-300")
    assert (ctx.get("facts") or {}).get("edit_memory", {}).get("ai_baseline_html") == "<p>ai</p>"


def test_save_draft_records_event(cal):
    sess = _enqueue(cal)
    cal.save_draft(quickcep_session_id="qc-300", draft_html="x", source="agent")
    with cal._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM cs_conversation_events "
            "WHERE session_id=? AND event_type='draft_saved' ORDER BY id DESC LIMIT 1",
            (sess["id"],),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["source"] == "agent"
    assert payload["attachments"] == 0


def test_save_draft_lock_check_refuses(cal):
    _enqueue(cal)

    def lock(_sess: dict[str, Any]) -> Optional[str]:
        return "autopilot countdown running"

    res = cal.save_draft(
        quickcep_session_id="qc-300",
        draft_html="edited",
        source="operator_edit",
        lock_check=lock,
    )
    assert res["success"] is False
    assert res["error"] == "draft_locked_autopilot"
    assert "autopilot" in res["error_detail"]
    # Draft must NOT be overwritten when locked.
    sess = cal.get_session(quickcep_session_id="qc-300")
    assert sess["draft_html"] is None


def test_save_draft_unknown_session(cal):
    res = cal.save_draft(quickcep_session_id="nope", draft_html="x")
    assert res["success"] is False
    assert res["error"] == "session not found"


def test_save_draft_overwrites_prior_draft(cal):
    _enqueue(cal)
    cal.save_draft(quickcep_session_id="qc-300", draft_html="v1", source="agent")
    cal.save_draft(quickcep_session_id="qc-300", draft_html="v2-edited", source="operator_edit")
    sess = cal.get_session(quickcep_session_id="qc-300")
    assert sess["draft_html"] == "v2-edited"
    assert sess["draft_source"] == "operator_edit"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
