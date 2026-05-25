"""SSE proxy: ``GET /campaigns/{cid}/agent-stream``.

The endpoint is a multi-run aggregator. For every ``product_campaign_runs``
row tied to (campaign_id, env) it opens a parallel gateway SSE stream and
re-wraps each frame with ``run_id`` + ``kind`` metadata. The initial
frame is a ``snapshot`` event carrying the registry of runs.

Why uvicorn instead of TestClient/ASGITransport:
   ``httpx.ASGITransport`` buffers the entire response body before
   yielding it to the client — fine for normal endpoints, fatal for SSE.
   ``StreamingResponse``-emitted chunks never reach the assertion code
   under TestClient either. The only reliable way to test streaming is a
   real HTTP server on a random port.

Stubs:
* ``get_conn`` → in-memory SQLite seeded with ``product_campaigns`` +
  ``product_campaign_runs``;
* ``get_bridge`` / ``get_gateway`` → no-op stand-ins;
* ``httpx.AsyncClient`` (inside the campaigns router) → a routing client
  that intercepts ``/v1/runs/`` URLs to serve canned chunks while real
  HTTP requests (made by the test runner) pass through.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import socket
import sqlite3
import threading
import time
from typing import Any, AsyncIterator, Callable

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.deps import current_user, get_bridge, get_conn, get_gateway  # noqa: E402
from app.routers import campaigns as campaigns_router  # noqa: E402


# ---------------------------------------------------------------------------
# DB / stub fixtures
# ---------------------------------------------------------------------------


def _seed_conn(run_id: str | None = "run-abc") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE product_campaigns (
            sku TEXT, campaign_id TEXT, env TEXT, run_id TEXT,
            test_mode_to TEXT, started_at TEXT, started_by_user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'running',
            PRIMARY KEY (campaign_id, env)
        )"""
    )
    conn.execute(
        """CREATE TABLE product_campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL, env TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            session_id TEXT, dedup_key TEXT,
            started_at TEXT NOT NULL, ended_at TEXT
        )"""
    )
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO product_campaigns (sku,campaign_id,env,run_id,started_at,status) "
        "VALUES (?,?,?,?,?,?)",
        ("SKU-1", "CID-1", "TEST", run_id, now, "running"),
    )
    if run_id:
        conn.execute(
            "INSERT INTO product_campaign_runs (campaign_id,env,run_id,kind,started_at) "
            "VALUES (?,?,?,?,?)",
            ("CID-1", "TEST", run_id, "outreach", now),
        )
    return conn


class _NullBridge:
    async def recent_events(self, *_a, **_kw) -> list[dict[str, Any]]:
        return []

    async def get_identity(self, *_a, **_kw) -> dict[str, Any]:
        return {}

    async def list_escalations(self, *_a, **_kw) -> list[dict[str, Any]]:
        return []


class _NullGateway:
    async def get_run(self, *_a, **_kw) -> dict[str, Any] | None:
        return None


class _FakeStreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def aiter_lines(self) -> AsyncIterator[str]:
        for chunk in self._chunks:
            for raw in chunk.decode("utf-8").split("\n"):
                yield raw


_real_httpx_async_client = httpx.AsyncClient


class _FakeClient:
    """Routing client. Only intercepts gateway ``/v1/runs/`` URLs; real
    HTTP requests (from the test driver hitting uvicorn) fall through to
    a real ``httpx.AsyncClient`` so SSE actually streams."""

    def __init__(self, chunks: list[bytes], status_code: int = 200, **kw) -> None:
        self._chunks = chunks
        self._status = status_code
        self._real = _real_httpx_async_client(**kw)

    async def __aenter__(self) -> "_FakeClient":
        await self._real.__aenter__()
        return self

    async def __aexit__(self, *a) -> bool:
        await self._real.__aexit__(*a)
        return False

    def stream(self, method: str, url: str, headers: dict | None = None, **kw):
        if "/v1/runs/" in str(url):
            return _FakeStreamResponse(self._chunks, status_code=self._status)
        return self._real.stream(method, url, headers=headers, **kw)

    def __getattr__(self, name: str):
        return getattr(self._real, name)


# ---------------------------------------------------------------------------
# Live-server harness
# ---------------------------------------------------------------------------


def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _LiveServer:
    """Run a uvicorn server in a background thread for the SSE tests."""

    def __init__(self, app: FastAPI) -> None:
        self.port = _pick_port()
        cfg = uvicorn.Config(
            app, host="127.0.0.1", port=self.port,
            log_level="warning", loop="asyncio",
        )
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "_LiveServer":
        self._thread.start()
        for _ in range(80):
            if self._server.started:
                break
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("uvicorn server did not start within 4 s")
        return self

    def __exit__(self, *_a) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=3.0)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _build_app(fake_factory: Callable[..., _FakeClient], *, run_id: str | None) -> FastAPI:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    # New _seed_conn per request — the producer's polling loop reads from
    # ``product_campaign_runs`` periodically so we need a connection that
    # survives the request lifetime.
    conn = _seed_conn(run_id=run_id)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_bridge] = lambda: _NullBridge()
    app.dependency_overrides[get_gateway] = lambda: _NullGateway()
    app.dependency_overrides[current_user] = lambda: {
        "id": 1, "email": "owner@console.app", "role": "owner", "is_active": 1,
    }
    # Module-level monkey-patch: the campaigns router calls
    # ``httpx.AsyncClient(timeout=None)`` for each gateway proxy. Replace
    # it with a fresh routing fake for every call so concurrent proxies
    # don't share state.
    campaigns_router.httpx.AsyncClient = lambda *a, **kw: fake_factory(*a, **kw)  # type: ignore[assignment]
    return app


# ---------------------------------------------------------------------------
# SSE parsing helpers
# ---------------------------------------------------------------------------


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any] | str]]:
    out: list[tuple[str, dict[str, Any] | str]] = []
    for raw_frame in body.split("\n\n"):
        if not raw_frame.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in raw_frame.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        data_str = "\n".join(data_lines)
        try:
            data: dict[str, Any] | str = json.loads(data_str)
        except json.JSONDecodeError:
            data = data_str
        out.append((event_name, data))
    return out


async def _read_until(
    base_url: str, *, marker_events: set[str], timeout: float = 5.0,
) -> str:
    accumulated: list[str] = []

    async def _drive() -> None:
        async with httpx.AsyncClient(base_url=base_url, timeout=None) as cli:
            async with cli.stream("GET", "/campaigns/CID-1/agent-stream?env=TEST") as resp:
                assert resp.status_code == 200, await resp.aread()
                async for chunk in resp.aiter_text():
                    accumulated.append(chunk)
                    seen = {n for n, _ in _parse_sse("".join(accumulated))}
                    if marker_events & seen:
                        return

    try:
        await asyncio.wait_for(_drive(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return "".join(accumulated)


def _run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agent_stream_emits_snapshot_then_forwards_gateway_chunks() -> None:
    chunks = [
        b"event: tool.started\ndata: {\"tool\":\"x\"}\n\n",
        b"event: reasoning.available\ndata: {\"text\":\"hmm\"}\n\n",
    ]
    app = _build_app(lambda *a, **kw: _FakeClient(chunks, **kw), run_id="run-abc")

    with _LiveServer(app) as srv:
        body = _run_async(_read_until(srv.base_url, marker_events={"run.closed"}))

    frames = _parse_sse(body)
    names = [n for n, _ in frames]
    assert "snapshot" in names, f"no snapshot in {names}"

    snap = next(d for n, d in frames if n == "snapshot")
    assert isinstance(snap, dict)
    assert snap["campaign_id"] == "CID-1"
    runs = snap.get("runs") or []
    assert any(r.get("run_id") == "run-abc" for r in runs)

    # Producer preserves the inner event name as the outer SSE event name
    # (only un-named "message" frames fall back to "run.event"); the wrapping
    # metadata (run_id / kind / payload) lives in the data body.
    wrapped = [
        d for n, d in frames
        if n in ("tool.started", "reasoning.available") and isinstance(d, dict)
    ]
    assert wrapped, f"expected re-wrapped gateway frames, got events={names}"
    inner_events = {d["event"] for d in wrapped}
    assert "tool.started" in inner_events
    assert "reasoning.available" in inner_events
    for d in wrapped:
        assert d["run_id"] == "run-abc"
        assert d["kind"] == "outreach"


def test_agent_stream_404_from_gateway_emits_run_evicted() -> None:
    app = _build_app(
        lambda *a, **kw: _FakeClient([b""], status_code=404, **kw),
        run_id="run-abc",
    )
    with _LiveServer(app) as srv:
        body = _run_async(_read_until(
            srv.base_url, marker_events={"run.evicted", "run.closed"},
        ))
    names = [n for n, _ in _parse_sse(body)]
    assert "snapshot" in names
    assert "run.evicted" in names, (
        f"expected run.evicted when gateway returns 404, got events={names}"
    )


def test_agent_stream_empty_registry_still_emits_snapshot() -> None:
    """No runs registered → endpoint still emits a snapshot with empty runs.
    The connection stays open polling for new runs; we only assert the
    initial snapshot arrives within the deadline.
    """
    app = _build_app(lambda *a, **kw: _FakeClient([], **kw), run_id=None)
    with _LiveServer(app) as srv:
        body = _run_async(_read_until(
            srv.base_url, marker_events={"snapshot"}, timeout=4.0,
        ))
    frames = _parse_sse(body)
    snap = next((d for n, d in frames if n == "snapshot"), None)
    assert isinstance(snap, dict), f"no snapshot frame; got {[n for n, _ in frames]}"
    assert snap.get("runs") == []
