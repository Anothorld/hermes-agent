"""Tests for escalation resume reply-draft gating."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.routers import escalations as escalations_mod  # noqa: E402


def test_needs_reply_draft_classifier_with_source_message_id():
    esc = {
        "campaign_id": "C1",
        "identity_id": 1,
        "resume_context": {
            "source": "classifier",
            "source_message_id": "msg-abc",
        },
    }
    assert escalations_mod._escalation_needs_reply_draft(esc) is True


def test_needs_reply_draft_classifier_inferred_inbound():
    esc = {
        "campaign_id": "C1",
        "identity_id": 1,
        "resume_context": {"source": "classifier"},
    }
    assert (
        escalations_mod._escalation_needs_reply_draft(
            esc,
            inferred_inbound_message_id="msg-inferred",
        )
        is True
    )


def test_needs_reply_draft_internal_without_inbound():
    esc = {
        "resume_context": {"source": "compensation_cap_breach"},
    }
    assert escalations_mod._escalation_needs_reply_draft(esc) is False


def test_needs_reply_draft_source_none_inferred_only_false():
    """Internal escalations must not draft just because timeline has inbound."""
    esc = {
        "campaign_id": "C1",
        "identity_id": 1,
        "resume_context": {"path": "reengagement", "last_outcome": "disputed"},
    }
    assert (
        escalations_mod._escalation_needs_reply_draft(
            esc,
            inferred_inbound_message_id="msg-inferred",
        )
        is False
    )


def test_needs_reply_draft_legacy_anchor_without_source_tag():
    esc = {
        "campaign_id": "C1",
        "identity_id": 1,
        "resume_context": {"source_message_id": "msg-legacy"},
    }
    assert escalations_mod._escalation_needs_reply_draft(esc) is True


def test_resume_draft_followup_states():
    assert escalations_mod._resume_draft_followup(
        needs_draft=True,
        require_draft=True,
        already_has_draft=False,
        draft_in_flight=False,
    ) == (True, "expected")
    assert escalations_mod._resume_draft_followup(
        needs_draft=True,
        require_draft=False,
        already_has_draft=True,
        draft_in_flight=False,
    ) == (False, "already_pending")
    assert escalations_mod._resume_draft_followup(
        needs_draft=True,
        require_draft=False,
        already_has_draft=False,
        draft_in_flight=True,
    ) == (False, "in_flight")


def test_needs_reply_draft_dispatcher_legacy():
    esc = {
        "campaign_id": "C1",
        "identity_id": 1,
        "resume_context": {
            "source": "dispatcher",
            "source_message_id": "msg-legacy",
        },
    }
    assert escalations_mod._escalation_needs_reply_draft(esc) is True


def test_linked_draft_phase_pre_answer():
    phase, can_approve, label = escalations_mod._linked_draft_phase("awaiting_answer")
    assert phase == "pre_answer"
    assert can_approve is False
    assert label == "升级回信预览"


def test_linked_draft_phase_post_resume():
    for state in ("answered", "resuming", "resolved"):
        phase, can_approve, label = escalations_mod._linked_draft_phase(state)
        assert phase == "post_resume"
        assert can_approve is True
        assert label == "升级恢复稿"


def test_inbound_message_id_prefers_resume_context():
    esc = {
        "resume_context": {"source_message_id": "ctx-msg"},
    }
    assert (
        escalations_mod._escalation_inbound_message_id(
            esc,
            inferred_inbound_message_id="inferred-msg",
        )
        == "ctx-msg"
    )
