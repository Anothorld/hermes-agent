from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import current_user, get_bridge
from app.routers import campaigns as campaigns_router


class _BridgeStub:
    async def get_shortlist(self, campaign_id: str, env: str) -> dict:
        assert campaign_id == "CID-1"
        assert env == "TEST"
        return {
            "campaign_id": campaign_id,
            "candidates": [
                {
                    "identity_id": 1,
                    "candidate_status": "selected_for_outreach",
                    "selected_at": "2026-06-01T09:00:00+00:00",
                    "updated_at": "2026-06-01T09:00:00+00:00",
                    "payload": {"audience_fit": 88},
                },
                {
                    "identity_id": 2,
                    "candidate_status": "discovered",
                    "updated_at": "2026-06-01T10:00:00+00:00",
                    "payload": {"audience_fit": 72},
                },
                {
                    "identity_id": 3,
                    "candidate_status": "archived",
                    "updated_at": "2026-06-01T10:30:00+00:00",
                    "payload": {},
                },
            ],
        }

    async def get_identity(self, identity_id: int) -> dict:
        return {"primary_handle": f"user{identity_id}", "display_name": f"User {identity_id}", "platform": "instagram"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "email": "owner@console.app",
        "role": "owner",
        "is_active": 1,
    }
    app.dependency_overrides[get_bridge] = lambda: _BridgeStub()
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
    }
    assert len(body["candidates"]) == 2

    pending = next(c for c in body["candidates"] if c["candidate_status"] != "selected_for_outreach")
    approved = next(c for c in body["candidates"] if c["candidate_status"] == "selected_for_outreach")
    assert pending["is_new_since_last_approval"] is True
    assert approved["is_new_since_last_approval"] is False
    assert pending["updated_at"] == "2026-06-01T10:00:00+00:00"
