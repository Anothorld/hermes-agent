"""Tests for auto-draft after approve-time email discovery."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.post_email_discover_draft import (
    maybe_trigger_outreach_draft_after_email_discover,
    parse_email_discover_session,
)


def test_parse_email_discover_session() -> None:
    assert parse_email_discover_session(
        "kol-email-discover:LIVE:42:tok-abc",
    ) == ("LIVE", 42)
    assert parse_email_discover_session("kol-campaign-outreach:LIVE:C1") is None
    assert parse_email_discover_session("kol-email-discover:LIVE:not-int:tok") is None


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _seed_conn(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "auto_draft.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE
        )"""
    )
    conn.execute(
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            payload_json TEXT,
            ts TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE product_campaigns (
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL,
            test_mode_to TEXT,
            PRIMARY KEY (campaign_id, env)
        )"""
    )
    conn.execute(
        """CREATE TABLE product_campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            session_id TEXT,
            dedup_key TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO users (id, email) VALUES (1, 'ops@example.com')"
    )
    conn.execute(
        "INSERT INTO product_campaigns (campaign_id, env, test_mode_to) "
        "VALUES ('CID-1', 'LIVE', NULL)"
    )
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target, payload_json, ts) "
        "VALUES (1, 'campaign.approve_shortlist', 'CID-1', ?, ?)",
        (json.dumps({"env": "LIVE"}), _utcnow()),
    )
    conn.commit()
    return conn


class _StubBridge:
    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = [
            {"identity_id": 42, "candidate_status": "selected_for_outreach"},
        ]
        self.identity: dict[str, Any] = {
            "primary_handle": "alice",
            "primary_email": "alice@example.com",
        }
        self.facts: dict[str, Any] = {}
        self.config: dict[str, Any] = {"product_display_name": "Sofa"}

    async def list_candidates(self, campaign_id: str, *, env: str) -> list[dict]:
        return self.candidates

    async def get_identity(self, identity_id: int) -> dict[str, Any]:
        return self.identity

    async def read_facts(
        self, identity_id: int, *, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        return {"facts": self.facts}

    async def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self.config


@pytest.mark.asyncio
async def test_auto_draft_starts_after_discover_complete(tmp_path) -> None:
    conn = _seed_conn(tmp_path)
    bridge = _StubBridge()
    gateway = MagicMock()
    gateway.start_run_with_retry = AsyncMock(return_value={"run_id": "run-draft-1"})
    gateway.launch_via_queue = AsyncMock(return_value={"run_id": "run-draft-1"})
    gateway.ensure_run_drained = MagicMock()

    with patch(
        "app.post_email_discover_draft._ensure_bridge_key_for_gateway",
        return_value=True,
    ):
        out = await maybe_trigger_outreach_draft_after_email_discover(
            bridge=bridge,
            gateway=gateway,
            conn=conn,
            campaign_id="CID-1",
            env="LIVE",
            session_id="kol-email-discover:LIVE:42:tok-1",
            discover_run_id="run-discover-1",
        )

    assert out is not None
    assert out["draft_run_id"] == "run-draft-1"
    assert out["identity_id"] == 42
    gateway.launch_via_queue.assert_awaited_once()
    launch_kwargs = gateway.launch_via_queue.await_args.kwargs
    assert launch_kwargs["session_id"] == "kol-campaign-draft:LIVE:CID-1:42"
    audit = conn.execute(
        "SELECT action FROM audit_log WHERE action=?",
        ("campaign.auto_draft_after_email_discover",),
    ).fetchone()
    assert audit is not None
    conn.close()


@pytest.mark.asyncio
async def test_auto_draft_skips_when_email_still_missing(tmp_path) -> None:
    conn = _seed_conn(tmp_path)
    bridge = _StubBridge()
    bridge.identity["primary_email"] = ""
    gateway = MagicMock()

    out = await maybe_trigger_outreach_draft_after_email_discover(
        bridge=bridge,
        gateway=gateway,
        conn=conn,
        campaign_id="CID-1",
        env="LIVE",
        session_id="kol-email-discover:LIVE:42:tok-1",
        discover_run_id="run-discover-1",
    )

    assert out is None
    conn.close()


@pytest.mark.asyncio
async def test_auto_draft_skips_when_draft_already_exists(tmp_path) -> None:
    conn = _seed_conn(tmp_path)
    bridge = _StubBridge()
    bridge.facts = {"offer.outreach_draft_ready": True}
    gateway = MagicMock()

    out = await maybe_trigger_outreach_draft_after_email_discover(
        bridge=bridge,
        gateway=gateway,
        conn=conn,
        campaign_id="CID-1",
        env="LIVE",
        session_id="kol-email-discover:LIVE:42:tok-1",
        discover_run_id="run-discover-1",
    )

    assert out is None
    conn.close()
