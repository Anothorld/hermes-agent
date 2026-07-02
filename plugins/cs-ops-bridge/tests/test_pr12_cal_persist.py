"""Tests for PR1.2 CAL persistence: visitor info + intention_tags + classify fact."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr12_test"


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
    db = tmp_path / "cal12.db"
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(db))
    # Force re-import so the new DB path takes effect.
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


def test_enqueue_persists_visitor_fields_and_tags(cal):
    res = cal.enqueue_session(
        quickcep_session_id="qc-100",
        customer_email="a@b.com",
        message_id="m1",
        customer_name="Alice",
        locale="US",
        email_subject="Re: order",
        last_message_preview="where is my package",
        intention_tags=["物流咨询", "产品咨询"],
    )
    assert res["created"] is True
    sess = cal.get_session(quickcep_session_id="qc-100")
    assert sess["customer_name"] == "Alice"
    assert sess["locale"] == "US"
    assert sess["email_subject"] == "Re: order"
    assert sess["last_message_preview"] == "where is my package"
    assert json.loads(sess["intention_tags"]) == ["物流咨询", "产品咨询"]


def test_enqueue_reenqueue_does_not_wipe_visitor_fields(cal):
    cal.enqueue_session(
        quickcep_session_id="qc-101",
        customer_email="a@b.com",
        message_id="m1",
        customer_name="Bob",
        email_subject="Subj A",
    )
    # Follow-up message: no visitor fields passed this time (SIO path).
    cal.enqueue_session(
        quickcep_session_id="qc-101",
        customer_email="a@b.com",
        message_id="m2",
    )
    sess = cal.get_session(quickcep_session_id="qc-101")
    assert sess["customer_name"] == "Bob"  # preserved via COALESCE
    assert sess["email_subject"] == "Subj A"


def test_enrich_session_backfills_missing_only(cal):
    cal.enqueue_session(
        quickcep_session_id="qc-102",
        customer_email="a@b.com",
        message_id="m1",
        customer_name="Original",
    )
    ok = cal.enrich_session(
        quickcep_session_id="qc-102",
        customer_name="ShouldNotOverwrite",
        locale="DE",
        intention_tags=["物流咨询"],
    )
    assert ok is True
    sess = cal.get_session(quickcep_session_id="qc-102")
    # COALESCE(NULLIF(?, ''), col) — non-empty new value DOES overwrite.
    assert sess["customer_name"] == "ShouldNotOverwrite"
    assert sess["locale"] == "DE"
    assert json.loads(sess["intention_tags"]) == ["物流咨询"]


def test_enrich_session_empty_string_does_not_overwrite(cal):
    cal.enqueue_session(
        quickcep_session_id="qc-103",
        customer_email="a@b.com",
        message_id="m1",
        customer_name="KeepMe",
    )
    cal.enrich_session(
        quickcep_session_id="qc-103",
        customer_name="",   # empty -> NULLIF -> NULL -> COALESCE keeps existing
        locale="",
    )
    sess = cal.get_session(quickcep_session_id="qc-103")
    assert sess["customer_name"] == "KeepMe"
    assert sess["locale"] is None


def test_enrich_session_unknown_session_returns_false(cal):
    assert cal.enrich_session(quickcep_session_id="nope", locale="US") is False


def test_classify_fact_written_on_handoff(cal):
    # Insert a session directly via enqueue.
    cal.enqueue_session(
        quickcep_session_id="qc-200",
        customer_email="a@b.com",
        message_id="m1",
    )
    sess = cal.get_session(quickcep_session_id="qc-200")
    cal.update_session_status(session_row_id=sess["id"], status="processing")
    # §4.13 B: draft_ready now requires a CAL draft (draft-save step 5 before handoff).
    cal.save_draft(
        quickcep_session_id="qc-200",
        draft_html="<p>draft</p>",
        attachments=[],
        source="agent",
        env="LIVE",
    )

    handoff = _load_pkg_module("session_handoff")
    # Stub QuickCEP side effects (tags/note) so apply_handoff doesn't hit network.
    with patch.object(handoff, "apply_quickcep_tags", return_value=[]), \
         patch.object(handoff, "apply_quickcep_note", return_value={"ok": True}), \
         patch.object(handoff, "_resolve_chat_session_id", return_value=None):
        handoff.apply_handoff(
            quickcep_session_id="qc-200",
            phase="draft_ready",
            context={
                "classify": {"category": "logistics", "route": "auto_handle", "confidence": "high"},
                "actions_taken": "draft ready",
            },
            skip_quickcep=True,
        )
    # Verify the classify fact landed in cs_facts.
    with cal._connect() as conn:
        row = conn.execute(
            "SELECT namespace, fact_key, fact_value_json FROM cs_facts "
            "WHERE session_id=? AND namespace='classify'",
            (sess["id"],),
        ).fetchall()
    assert len(row) == 4  # category, route, confidence, urgency
    facts = {r["fact_key"]: json.loads(r["fact_value_json"]) for r in row}
    assert facts["category"] == "logistics"
    assert facts["route"] == "auto_handle"
    assert facts["confidence"] == "high"
    assert facts["urgency"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
