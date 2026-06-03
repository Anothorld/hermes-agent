"""Tests for POST .../followup-draft campaign route."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.routers import campaigns as campaigns_mod  # noqa: E402


def test_compose_followup_brief_includes_topic():
    brief = campaigns_mod._compose_followup_brief(
        campaign_id="TS8319",
        env="TEST",
        identity_id=7,
        handle="alice",
        actor_email="op@brand.com",
        operator_topic="催拍摄时间",
        test_mode_to="test@brand.com",
    )
    assert "campaign_followup_draft" in brief
    assert "催拍摄时间" in brief
    assert "identity_id: 7" in brief


@pytest.mark.asyncio
async def test_followup_draft_rejects_empty_topic_via_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        campaigns_mod.FollowupDraftBody(env="TEST", topic="")


@pytest.mark.asyncio
async def test_followup_draft_requires_outreach_sent(monkeypatch):
    bridge = AsyncMock()
    bridge.get_identity.return_value = {"primary_email": "kol@x.com"}
    bridge.read_facts.return_value = {"facts": {"offer.outreach_sent": False}}
    gateway = AsyncMock()
    conn = MagicMock()
    user = {"id": 1, "email": "op@brand.com"}

    monkeypatch.setattr(
        campaigns_mod,
        "campaign_lock",
        AsyncMock(return_value=asyncio.Lock()),
    )
    monkeypatch.setattr(
        campaigns_mod,
        "_campaign_run_in_flight",
        AsyncMock(return_value=(False, None, None)),
    )
    monkeypatch.setattr(campaigns_mod, "get_inflight_run", lambda *_a, **_k: None)
    monkeypatch.setattr(campaigns_mod, "assert_campaign_config_complete", AsyncMock())

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await campaigns_mod.followup_draft(
            "C1",
            1,
            campaigns_mod.FollowupDraftBody(env="TEST", topic="催一下"),
            bridge,
            conn,
            user,
            gateway,
        )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "outreach_not_sent"
