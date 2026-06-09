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
        gmail_sent_thread_id="19e81ff6def3b65f",
        gmail_thread_id="19e81ff6def3b664a",
    )
    assert "campaign_followup_draft" in brief
    assert "催拍摄时间" in brief
    assert "identity_id: 7" in brief
    assert "gmail_sent_thread_id: 19e81ff6def3b65f" in brief
    assert "reply in the existing Gmail thread" in brief


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


def test_is_initial_outreach_reply_draft_detects_cold_outreach_skill():
    draft = {
        "child_skill": "kol-cold-outreach",
        "source_message_id": "draft:outreach_TS8319_7",
        "thread_id": "outreach_TS8319_7",
    }
    assert campaigns_mod._is_initial_outreach_reply_draft(
        draft, campaign_id="TS8319", identity_id=7,
    )


def test_approved_reply_draft_blocks_followup_skips_sent_initial_outreach():
    draft = {
        "decision": "approved",
        "child_skill": "kol-cold-outreach",
        "gmail_draft": {"draft_id": "r-old", "thread_id": "t1"},
    }
    assert not campaigns_mod._approved_reply_draft_blocks_followup(
        draft, campaign_id="C1", identity_id=1,
    )


def test_approved_reply_draft_blocks_followup_for_unsent_reply():
    draft = {
        "decision": "approved",
        "child_skill": "kol-reply-synthesizer",
        "source_message_id": "19e84b2d4cf91067",
        "gmail_draft": {"draft_id": "r-reply", "thread_id": "t2"},
    }
    assert campaigns_mod._approved_reply_draft_blocks_followup(
        draft, campaign_id="C1", identity_id=1,
    )


@pytest.mark.asyncio
async def test_followup_draft_allows_after_initial_outreach_approved_and_sent(monkeypatch):
    bridge = AsyncMock()
    bridge.get_identity.return_value = {"primary_email": "kol@x.com", "primary_handle": "alice"}
    bridge.read_facts.return_value = {
        "facts": {
            "offer.outreach_sent": True,
            "offer.gmail_draft_id": "r-5206072674356124082",
            "approval.reply_draft": {
                "decision": "approved",
                "child_skill": "kol-cold-outreach",
                "source_message_id": "draft:outreach_C1_1",
                "gmail_draft": {"draft_id": "r-5206072674356124082"},
            },
        },
    }
    gateway = AsyncMock()
    gateway.start_run_with_retry = AsyncMock(return_value={"run_id": "run-followup-1"})
    gateway.launch_via_queue = AsyncMock(return_value={"run_id": "run-followup-1"})
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
    monkeypatch.setattr(campaigns_mod, "register_run", MagicMock())
    monkeypatch.setattr(campaigns_mod, "write_audit", MagicMock())
    monkeypatch.setattr(campaigns_mod, "ensure_gateway_bridge_key", lambda: None)

    conn.execute.return_value.fetchone.return_value = {"sku": "SKU1", "test_mode_to": "t@x.com"}

    out = await campaigns_mod.followup_draft(
        "C1",
        1,
        campaigns_mod.FollowupDraftBody(env="TEST", topic="催拍摄"),
        bridge,
        conn,
        user,
        gateway,
    )
    assert out["run_id"] == "run-followup-1"
    gateway.launch_via_queue.assert_awaited_once()
