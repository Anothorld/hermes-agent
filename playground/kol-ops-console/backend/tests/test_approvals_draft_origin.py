"""Tests for approval reply-draft origin badges."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.routers import approvals as approvals_mod  # noqa: E402


def test_draft_origin_escalation_resume():
    origin, label = approvals_mod._derive_draft_origin(
        "approval.reply_draft",
        {"linked_escalation_id": 109, "child_skill": "kol-compensation-negotiator"},
    )
    assert origin == "escalation_resume"
    assert label == "升级恢复稿"


def test_draft_origin_chase_placeholder_legacy():
    origin, label = approvals_mod._derive_draft_origin(
        "approval.reply_draft",
        {"chase_supersede": True, "child_skill": "kol-reply-synthesizer"},
    )
    assert origin == "chase_placeholder"
    assert label == "追信占位(已废弃)"


def test_draft_origin_chase_followup_regenerated():
    origin, label = approvals_mod._derive_draft_origin(
        "approval.reply_draft",
        {
            "chase_supersede": {
                "prior_source_message_id": "MSG1",
                "superseded_for_follow_up": True,
            },
            "child_skill": "kol-reply-synthesizer",
        },
    )
    assert origin == "chase_followup"
    assert label == "追信换新稿"


def test_draft_origin_proactive_followup():
    origin, label = approvals_mod._derive_draft_origin(
        "approval.reply_draft",
        {"child_skill": "kol-proactive-followup", "kind": "proactive_followup"},
    )
    assert origin == "proactive_followup"
    assert label == "操作员追信"


def test_draft_origin_inbound_auto():
    origin, label = approvals_mod._derive_draft_origin(
        "approval.reply_draft",
        {"child_skill": "kol-compensation-negotiator"},
    )
    assert origin == "inbound_auto"
    assert label == "KOL回信自动"
