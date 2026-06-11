"""``GET /campaigns/{id}/shortlist`` — snapshot counts + new-candidate markers."""

from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import current_user, get_bridge, get_conn
from app.routers import campaigns as campaigns_router


class _BridgeStub:
    async def list_candidate_handles(self, campaign_id: str, *, env: str) -> list[dict]:
        assert campaign_id == "CID-1"
        assert env == "TEST"
        return [
            {
                "identity_id": 1,
                "handle": "user1",
                "platform": "instagram",
                "candidate_status": "selected_for_outreach",
                "selected_at": "2026-06-01T09:00:00+00:00",
                "updated_at": "2026-06-01T09:00:00+00:00",
                "payload": {"audience_fit": 88},
            },
            {
                "identity_id": 2,
                "handle": "user2",
                "platform": "instagram",
                "candidate_status": "discovered",
                "updated_at": "2026-06-01T10:00:00+00:00",
                "payload": {"audience_fit": 72},
            },
            {
                "identity_id": 3,
                "handle": "user3",
                "platform": "instagram",
                "candidate_status": "archived",
                "updated_at": "2026-06-01T10:30:00+00:00",
                "payload": {},
            },
        ]

    async def batch_facts_subset(self, **kwargs) -> dict:
        return {}

    async def batch_outreach_touch(self, identity_ids, *, env: str) -> dict:
        return {"items": {}}

    async def batch_internal_touch_count(self, **kwargs) -> dict:
        return {"items": {}}


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute(
        """CREATE TABLE product_campaigns (
            sku TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL,
            PRIMARY KEY (campaign_id, env)
        )"""
    )
    conn.execute(
        "CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT, pitch_md TEXT)"
    )
    return conn


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    conn = _seed_conn()
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "email": "owner@console.app",
        "role": "owner",
        "is_active": 1,
    }
    app.dependency_overrides[get_bridge] = lambda: _BridgeStub()
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def test_shortlist_endpoint_returns_snapshot_counts_and_new_markers() -> None:
    client = _client()

    resp = client.get("/campaigns/CID-1/shortlist?env=TEST")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["snapshot_ts"] == "2026-06-01T10:00:00+00:00"
    assert body["counts"] == {
        "pending": 1,
        "already_approved": 1,
        "rejected_or_archived_hidden": 1,
        "prior_sku_approved_in_pending": 0,
        "pending_actionable": 1,
    }
    assert len(body["candidates"]) == 1

    pending = body["candidates"][0]
    assert pending["handle"] == "user2"
    assert pending["candidate_status"] == "discovered"
    assert pending["is_new_since_last_approval"] is True
    assert pending["updated_at"] == "2026-06-01T10:00:00+00:00"
    # No sibling campaign → nothing flagged as cross-campaign duplicate.
    assert pending["prior_sku_campaign_approval"] is None
