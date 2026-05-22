"""SSE proxy: ``GET /campaigns/{cid}/agent-stream``.

The endpoint emits an initial ``snapshot`` frame, then forwards every
byte of the gateway's ``/v1/runs/{run_id}/events`` SSE feed.  We stub
both the SQLite ``get_conn`` dependency (so the run_id lookup works
without seeding a real DB) and the ``httpx.AsyncClient.stream`` call
(so no actual gateway is required).
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_conn  # noqa: E402
from app.routers import campaigns as campaigns_router  # noqa: E402


def _seed_conn(run_id: str | None = "run-abc") -> sqlite3.Connection:
    # check_same_thread=False — Starlette's TestClient invokes the
    # endpoint on a worker thread distinct from the one that created
    # the connection (matching what the real ``get_conn`` dep does in
    # ``app/db.py``).
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE product_campaigns (
            campaign_id TEXT, env TEXT, run_id TEXT,
            PRIMARY KEY (campaign_id, env)
        )"""
    )
    conn.execute(
        "INSERT INTO product_campaigns (campaign_id, env, run_id) VALUES ('CID-1', 'TEST', ?)",
        (run_id,),
    )
    return conn


class _FakeStreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_raw(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return b"".join(self._chunks)


class _FakeClient:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self._status = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method: str, url: str, headers: dict | None = None):
        return _FakeStreamResponse(self._chunks, status_code=self._status)


def _build_app(fake_client, *, run_id: str | None = "run-abc") -> FastAPI:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    conn = _seed_conn(run_id=run_id)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[current_user] = lambda: {
        "id": 1, "email": "owner@console.app", "role": "owner", "is_active": 1,
    }
    # Patch the module-level httpx.AsyncClient so the proxy uses our stub.
    campaigns_router.httpx.AsyncClient = lambda *_a, **_kw: fake_client  # type: ignore[assignment]
    return app


def test_agent_stream_emits_snapshot_then_forwards_gateway_chunks(monkeypatch):
    chunks = [
        b"event: tool.started\ndata: {\"tool\":\"x\"}\n\n",
        b"event: reasoning.available\ndata: {\"text\":\"hmm\"}\n\n",
    ]
    fake = _FakeClient(chunks)
    app = _build_app(fake)

    with TestClient(app) as client:
        r = client.get("/campaigns/CID-1/agent-stream?env=TEST")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.text
        # Snapshot frame is always first.
        assert "event: snapshot" in body
        assert "\"run_id\": \"run-abc\"" in body
        # Gateway chunks are forwarded verbatim.
        assert "event: tool.started" in body
        assert "event: reasoning.available" in body


def test_agent_stream_no_run_emits_closed_snapshot(monkeypatch):
    fake = _FakeClient([])  # never reached
    app = _build_app(fake, run_id=None)
    with TestClient(app) as client:
        r = client.get("/campaigns/CID-1/agent-stream?env=TEST")
        assert r.status_code == 200
        assert "event: snapshot" in r.text
        assert "event: closed" in r.text
        assert "no_run" in r.text


def test_agent_stream_404_from_gateway_emits_closed(monkeypatch):
    fake = _FakeClient([b""], status_code=404)
    app = _build_app(fake)
    with TestClient(app) as client:
        r = client.get("/campaigns/CID-1/agent-stream?env=TEST")
        assert r.status_code == 200
        # Snapshot still emitted before the gateway request.
        assert "event: snapshot" in r.text
        assert "event: closed" in r.text
        assert "run_evicted" in r.text
