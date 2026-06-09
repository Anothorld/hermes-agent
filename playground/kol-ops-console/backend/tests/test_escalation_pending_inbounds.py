"""Tests for escalation pending inbound collection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.routers import escalations as escalations_mod  # noqa: E402


def test_collect_pending_inbounds_from_resume_context():
    escalation = {
        "created_at": "2026-06-09T10:00:00+00:00",
        "resume_context": {
            "pending_inbounds": [
                {"message_id": "MSG1", "role": "trigger"},
                {"message_id": "MSG2", "role": "followup", "snippet": "Any update?"},
            ],
        },
    }
    events = [
        {
            "id": 1,
            "ts": "2026-06-09T09:55:00+00:00",
            "event_type": "kol_inbound_reply",
            "payload": {
                "message_id": "MSG1",
                "subject": "Re: offer",
                "body": "Can we change scope?",
            },
        },
        {
            "id": 2,
            "ts": "2026-06-09T10:30:00+00:00",
            "event_type": "kol_inbound_reply",
            "payload": {
                "message_id": "MSG2",
                "subject": "Re: offer",
                "body": "Following up please",
            },
        },
    ]
    rows = escalations_mod._collect_pending_inbounds(escalation, events)
    assert len(rows) == 2
    assert rows[0]["label"] == "触发升级"
    assert rows[0]["body"] == "Can we change scope?"
    assert rows[1]["label"] == "追信（待处理）"
    assert rows[1]["body"] == "Following up please"
