"""Tests for approval.reply_draft kind classification."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.reply_draft_kind import is_initial_outreach_reply_draft  # noqa: E402
from app.routers import approvals as approvals_mod  # noqa: E402


def test_is_initial_outreach_by_child_skill():
    assert is_initial_outreach_reply_draft(
        {"child_skill": "kol-cold-outreach"},
        campaign_id="camp-1",
        identity_id=42,
    )


def test_is_inbound_reply_by_child_skill():
    assert not is_initial_outreach_reply_draft(
        {"child_skill": "kol-reply-synthesizer"},
        campaign_id="camp-1",
        identity_id=42,
    )


def test_to_row_sets_reply_draft_kind_initial():
    row = approvals_mod._to_row(
        {
            "identity_id": 7,
            "campaign_id": "sku-a",
            "fact_key": "approval.reply_draft",
            "value": {
                "child_skill": "kol-cold-outreach",
                "decision": "pending",
            },
            "captured_at": "2026-06-10T12:00:00Z",
        },
        {},
    )
    assert row["reply_draft_kind"] == "initial_outreach"


def test_to_row_sets_reply_draft_kind_inbound():
    row = approvals_mod._to_row(
        {
            "identity_id": 7,
            "campaign_id": "sku-a",
            "fact_key": "approval.reply_draft",
            "value": {
                "child_skill": "kol-compensation-negotiator",
                "decision": "pending",
            },
            "captured_at": "2026-06-10T12:00:00Z",
        },
        {},
    )
    assert row["reply_draft_kind"] == "inbound_reply"
