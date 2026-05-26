"""Tests for the CAL ``campaign_config`` completeness contract.

Three layers:

1. **BridgeClient transport retry** — :meth:`BridgeClient._req` retries
   on ``httpx.HTTPError`` exactly once when ``retry=1`` is requested,
   and does NOT retry on HTTP 4xx/5xx response codes. The latter
   guarantees we don't silently double-fire idempotent-but-still-
   side-effecting writes on deterministic failures.

2. **Launch hard-fail** — ``POST /campaigns/{id}/start`` returns 502
   ``cal_upsert_failed`` when the bridge upsert raises. Previously
   this was an audit-warning-and-continue; that path is what caused
   escalation #79 (campaign `SEB8008-20260525`).

3. **Fresh-session draft gates** — ``POST
   /campaigns/{id}/identities/{iid}/redraft-outreach`` returns 400
   ``campaign_config_incomplete`` when CAL is missing
   ``product_display_name``. With the field present, it proceeds to
   ``gateway.start_run``.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.bridge_client import BridgeClient, BridgeError  # noqa: E402
from app.deps import current_user, get_bridge, get_conn, get_gateway  # noqa: E402
from app.routers import campaigns as campaigns_router  # noqa: E402


# ---------------------------------------------------------------------------
# Layer 1: BridgeClient._req retry semantics
# ---------------------------------------------------------------------------


class _FakeHttpxClient:
    """Minimal stand-in for ``httpx.AsyncClient`` that returns a queue
    of pre-baked responses or raises pre-baked exceptions per call."""

    def __init__(self, behaviors: list[Any]) -> None:
        self.behaviors = list(behaviors)
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, **_kwargs: Any) -> Any:
        self.calls.append((method, url))
        if not self.behaviors:
            raise AssertionError("FakeHttpxClient out of pre-baked behaviors")
        nxt = self.behaviors.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"ok": True},
        headers={"content-type": "application/json"},
    )


def _500_response() -> httpx.Response:
    return httpx.Response(
        500,
        json={"err": "server"},
        headers={"content-type": "application/json"},
    )


def _make_client(behaviors: list[Any]) -> BridgeClient:
    client = BridgeClient.__new__(BridgeClient)
    client._client = _FakeHttpxClient(behaviors)
    client._base = "http://test"
    client._headers = {}
    return client


@pytest.mark.asyncio
async def test_req_retry_absorbs_one_transient_error() -> None:
    """First call raises ``httpx.ConnectError``; second succeeds. The
    operator should never see this — retry=1 absorbs the blip."""
    client = _make_client([httpx.ConnectError("boom"), _ok_response()])
    out = await client._req("PUT", "/campaigns/foo", json={}, retry=1)
    assert out == {"ok": True}
    assert len(client._client.calls) == 2


@pytest.mark.asyncio
async def test_req_raises_when_retry_exhausted() -> None:
    """Two consecutive transient errors with ``retry=1`` exhausts the
    budget and surfaces a ``BridgeError(502)``. Launch caller turns
    this into ``cal_upsert_failed``."""
    client = _make_client([
        httpx.ConnectError("a"),
        httpx.ConnectError("b"),
    ])
    with pytest.raises(BridgeError) as exc_info:
        await client._req("PUT", "/campaigns/foo", json={}, retry=1)
    assert exc_info.value.status == 502
    assert len(client._client.calls) == 2


@pytest.mark.asyncio
async def test_req_does_not_retry_on_http_5xx() -> None:
    """HTTP 5xx is a deterministic server-side decision (bad payload,
    schema mismatch, etc.). Retrying wastes work and risks duplicate
    side effects. The retry budget must apply ONLY to transport
    errors."""
    client = _make_client([_500_response()])
    with pytest.raises(BridgeError):
        await client._req("PUT", "/campaigns/foo", json={}, retry=1)
    assert len(client._client.calls) == 1  # NOT retried


# ---------------------------------------------------------------------------
# Layer 2 + 3: route-level integration
# ---------------------------------------------------------------------------


class _StubBridge:
    """In-process bridge stub used by the FastAPI app fixtures below."""

    def __init__(self) -> None:
        self.upsert_calls: list[tuple[str, dict[str, Any]]] = []
        self.upsert_should_raise: BridgeError | None = None
        # ``get_campaign_returns`` is whatever ``bridge.get_campaign``
        # should yield in this test. ``None`` = simulate the 404 path
        # (BridgeError status=404).
        self.get_campaign_returns: dict[str, Any] | None = {
            "campaign_id": "CID-1",
            "product_display_name": "the test sofa",
            "label": "Test Sofa",
            "paid_ceiling": 1500.0,
        }
        self.identity_map: dict[int, dict[str, Any]] = {}
        self.facts_by_identity: dict[int, dict[str, Any]] = {}
        self.candidates: list[dict[str, Any]] = []

    async def upsert_campaign(self, campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.upsert_should_raise is not None:
            raise self.upsert_should_raise
        self.upsert_calls.append((campaign_id, body))
        return {"ok": True}

    async def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        if self.get_campaign_returns is None:
            raise BridgeError(404, "campaign not found")
        return dict(self.get_campaign_returns)

    async def list_candidates(self, campaign_id: str, *, env: str) -> list[dict[str, Any]]:
        return list(self.candidates)

    async def get_identity(self, identity_id: int) -> dict[str, Any]:
        return self.identity_map.get(identity_id, {})

    async def read_facts(self, identity_id: int, *,
                         campaign_id: str | None = None,
                         env: str = "LIVE") -> dict[str, Any]:
        return {"facts": self.facts_by_identity.get(identity_id, {})}


class _StubGateway:
    def __init__(self) -> None:
        self.runs_started: list[dict[str, Any]] = []
        self._next = 0

    async def start_run(self, *, input: str, instructions: str | None = None,
                        session_id: str | None = None,
                        model: str | None = None) -> dict[str, Any]:
        self._next += 1
        run_id = f"run-{self._next}"
        self.runs_started.append({"run_id": run_id, "session_id": session_id})
        return {"run_id": run_id, "status": "queued"}

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return {"status": "running"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """CREATE TABLE products (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT,
            tags_json TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            pitch_md TEXT,
            selling_points TEXT,
            variants_json TEXT,
            default_budget_per_kol REAL,
            default_budget_total REAL,
            default_absolute_floor REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE product_campaigns (
            sku TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL CHECK (env IN ('LIVE','TEST')),
            run_id TEXT,
            test_mode_to TEXT,
            started_at TEXT NOT NULL,
            started_by_user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','closed','cancelled')),
            target_floor INTEGER,
            baseline_candidate_count INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            floor_unmet_reason TEXT,
            gate_run_id TEXT,
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
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            payload_json TEXT,
            ts TEXT NOT NULL
        )"""
    )
    # Insert one product so the launch endpoint can resolve sku → row.
    conn.execute(
        "INSERT INTO products (sku, name, url, tags_json, created_at) "
        "VALUES (?,?,?,?,?)",
        ("SKU-1", "Test Sofa", "https://example.com/sku1", "[]", _now()),
    )
    return conn


def _seed_running_campaign(conn: sqlite3.Connection) -> None:
    """Used by redraft tests: simulate that CID-1 was started cleanly
    earlier so the redraft endpoint can find the product_campaigns row.
    """
    conn.execute(
        """INSERT INTO product_campaigns
             (sku, campaign_id, env, run_id, test_mode_to, started_at,
              started_by_user_id, status)
           VALUES (?,?,?,?,?,?,?, ?)""",
        ("SKU-1", "CID-1", "LIVE", "run-prior", None, _now(), 1, "closed"),
    )


def _build_app(conn: sqlite3.Connection, bridge: _StubBridge,
               gateway: _StubGateway) -> FastAPI:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_bridge] = lambda: bridge
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[current_user] = lambda: {
        "id": 1, "email": "owner@console.app", "role": "owner", "is_active": 1,
    }
    return app


def _launch_body() -> dict[str, Any]:
    return {
        "product_sku": "SKU-1",
        "product_display_name": "the test sofa",
        "budget_per_kol": 1500.0,
        "absolute_floor": 1500.0,
        "budget_total": 45000.0,
        "headcount_target": 5,
        "env": "LIVE",
    }


def test_launch_502_when_upsert_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The previous audit-warning fallback would have returned 200 and
    spawned an agent run; the agent would later open
    campaign_config_missing_required_product_facts (escalation #79
    scenario). After this fix, the launch refuses outright."""
    conn = _seed_conn()
    bridge = _StubBridge()
    bridge.upsert_should_raise = BridgeError(502, "bridge unreachable")
    gateway = _StubGateway()
    # ``ensure_gateway_bridge_key`` reads from settings; bypass it so
    # the test doesn't require a real bridge_key file.
    from app.routers import campaigns as _c
    monkeypatch.setattr(_c, "ensure_gateway_bridge_key", lambda: "stub")

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/start", json=_launch_body(),
    )

    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "cal_upsert_failed"
    assert "retry_after_seconds" in detail
    # No agent run was spawned — the launch refused before
    # ``gateway.start_run``.
    assert gateway.runs_started == []


def test_redraft_400_when_cal_missing_product_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fresh-session redraft must refuse before invoking the
    gateway when CAL is missing product_display_name. Operator gets a
    pointer to the fix endpoint instead of an agent escalation."""
    conn = _seed_conn()
    _seed_running_campaign(conn)
    bridge = _StubBridge()
    bridge.get_campaign_returns = {
        "campaign_id": "CID-1",
        "product_display_name": None,
        "label": "Test Sofa",
    }
    bridge.identity_map[42] = {"primary_handle": "alice",
                               "primary_email": "alice@x.com"}
    gateway = _StubGateway()
    from app.routers import campaigns as _c
    monkeypatch.setattr(_c, "ensure_gateway_bridge_key", lambda: "stub")

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/identities/42/redraft-outreach",
        json={"env": "LIVE"},
    )

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "campaign_config_incomplete"
    assert "product_display_name" in detail["missing_fields"]
    assert detail["fix_endpoint"].startswith("PATCH /campaigns/CID-1/config")
    # No agent run was spawned — the redraft refused at the gate.
    assert gateway.runs_started == []


def test_redraft_proceeds_when_cal_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With CAL fully populated, the redraft proceeds end-to-end and
    spawns a gateway run."""
    conn = _seed_conn()
    _seed_running_campaign(conn)
    bridge = _StubBridge()
    bridge.identity_map[42] = {"primary_handle": "alice",
                               "primary_email": "alice@x.com"}
    gateway = _StubGateway()
    from app.routers import campaigns as _c
    monkeypatch.setattr(_c, "ensure_gateway_bridge_key", lambda: "stub")

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/identities/42/redraft-outreach",
        json={"env": "LIVE"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity_id"] == 42
    assert body["campaign_id"] == "CID-1"
    assert len(gateway.runs_started) == 1
    # Session namespace stays draft-scoped (the bug we're protecting
    # against: a draft session must not be mistaken for a campaign
    # resume).
    assert gateway.runs_started[0]["session_id"].startswith(
        "kol-campaign-draft:"
    )
