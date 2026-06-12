"""Tests for escalation operator hub helpers."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.escalation_hub import (  # noqa: E402
    build_completion_summary,
    build_topic_cards,
    build_workflow_steps,
    compute_workflow_step,
    parse_pending_topic_segments,
)


def test_parse_pending_topic_segments():
    raw = (
        "Matt: organic-only — operator decision needed (escalation 1572); "
        "Alyssa: accepts 15% discount, wants next steps"
    )
    parts = parse_pending_topic_segments(raw)
    assert len(parts) == 2
    assert parts[0].startswith("Matt:")


def test_build_topic_cards_from_pending_topics():
    esc = {"id": 1572, "reason": "usage rights"}
    facts = {
        "approval.pending_topics": (
            "Matt: organic-only — operator decision needed (escalation 1572); "
            "Alyssa: accepts 15% discount"
        ),
    }
    cards = build_topic_cards(esc, facts)
    assert len(cards) == 2
    assert cards[0]["status"] == "needs_decision"
    assert cards[0]["status_label"] == "需你决定"
    assert cards[1]["status"] == "auto_reply"
    assert cards[1]["label"] == "Alyssa"


def test_build_topic_cards_fallback_trigger():
    esc = {
        "id": 99,
        "resume_context": {"kol_quote": "We need organic only."},
        "question_to_operator": "是否接受 organic-only？",
    }
    cards = build_topic_cards(esc, {})
    assert len(cards) == 1
    assert cards[0]["status"] == "needs_decision"
    assert "organic" in cards[0]["summary"].lower()


def test_workflow_step_pre_answer_with_draft():
    assert compute_workflow_step(
        escalation_state="awaiting_answer",
        has_draft=True,
        draft_phase="pre_answer",
        can_approve=False,
    ) == 3


def test_workflow_step_awaiting_answer_no_draft():
    assert compute_workflow_step(
        escalation_state="awaiting_answer",
        has_draft=False,
        draft_phase=None,
        can_approve=False,
    ) == 2


def test_completion_summary_draft_approved():
    esc = {"id": 1572, "state": "resolved"}
    facts = {
        "approval.reply_draft": {
            "decision": "approved",
            "linked_escalation_id": 1572,
            "gmail_draft": {"draft_id": "r-abc", "thread_id": "th-1"},
        },
    }
    out = build_completion_summary(esc, facts, has_pending_draft=False)
    assert out is not None
    assert out["status"] == "draft_approved"
    assert out["gmail_draft_id"] == "r-abc"
    assert out["gmail_thread_id"] == "th-1"


def test_completion_summary_hidden_while_pending_draft():
    esc = {"id": 1, "state": "resolved"}
    facts = {"approval.reply_draft": {"decision": "approved"}}
    assert build_completion_summary(esc, facts, has_pending_draft=True) is None


def test_workflow_step_resolved_can_approve():
    wf = build_workflow_steps(
        escalation_state="resolved",
        has_draft=True,
        draft_phase="post_resume",
        can_approve=True,
    )
    assert wf["active_step"] == 5
    assert wf["steps"][4]["status"] == "active"
    assert wf["steps"][0]["status"] == "done"
