"""Concurrency contract for the discovery quantity-gate.

These tests assert the invariants that the fix for the
approve-during-rediscover race relies on:

* ``/approve-shortlist`` returns 409 when ``gate_run_id`` is set on the
  campaign row (regardless of the row's ``run_id`` / ``status``).
* ``/approve-shortlist`` returns 409 when the row's latest run is still
  in flight on the gateway.
* ``/rediscover`` returns 409 in the same conditions and additionally
  blocks during the discovery-gate auto-retry window.
* The discovery gate only fires when ``gate_run_id`` corresponds to the
  just-terminated run, NOT when an approve-driven outreach run reaches
  terminal — so approve-driven runs cannot trigger spurious auto-retries.

The tests stub the bridge + gateway in-process; they do NOT spin up the
SSE proxy (covered by ``test_agent_stream.py``).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_bridge, get_conn, get_gateway  # noqa: E402
from app.routers import campaigns as campaigns_router  # noqa: E402
from app.routers import products as products_router  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubBridge:
    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.identity_map: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.approve_calls: list[tuple[str, dict[str, Any]]] = []
        self.upsert_calls: list[tuple[str, dict[str, Any]]] = []
        self.route_calls: list[tuple[str, dict[str, Any]]] = []
        self.escalations: list[dict[str, Any]] = []
        self.route_response: dict[str, Any] = {"ok": True}

    async def list_candidates(self, campaign_id: str, *, env: str):
        return list(self.candidates)

    async def get_identity(self, identity_id: int):
        return self.identity_map.get(identity_id, {})

    async def upsert_campaign(self, campaign_id: str, body: dict):
        self.upsert_calls.append((campaign_id, body))
        return {"ok": True}

    async def route_discovery(self, campaign_id: str, body: dict):
        self.route_calls.append((campaign_id, body))
        return dict(self.route_response)

    async def approve_shortlist(self, campaign_id: str, body: dict):
        self.approve_calls.append((campaign_id, body))
        return {"ok": True, "campaign_id": campaign_id}

    async def write_event(self, body: dict):
        self.events.append(body)
        return {"event_id": len(self.events)}

    async def recent_events(self, env: str, limit: int = 200):
        return []

    async def open_escalation(self, body: dict):
        self.escalations.append(body)
        return {"ok": True}


class _StubGateway:
    """Tracks every ``start_run`` invocation and lets the test pin the
    ``get_run`` reply per run_id."""

    def __init__(self) -> None:
        self.runs_started: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self._next_id = 0

    def _mint_id(self) -> str:
        self._next_id += 1
        return f"run-{self._next_id}"

    async def start_run(self, *, input: str, instructions: str | None = None,
                        session_id: str | None = None, model: str | None = None):
        new_id = self._mint_id()
        self.runs_started.append({
            "run_id": new_id,
            "input": input,
            "session_id": session_id,
        })
        # Default: newly started runs are "running".
        self.states[new_id] = {"status": "running"}
        return {"run_id": new_id, "status": "queued"}

    async def get_run(self, run_id: str):
        return self.states.get(run_id)

    async def stop_run(self, run_id: str):
        if run_id in self.states:
            self.states[run_id]["status"] = "cancelled"
        return {"status": "stopping"}


def _seed_conn() -> sqlite3.Connection:
    """In-memory SQLite seeded with the current schema. Mirrors
    ``app.db._connect`` minus the on-disk path so tests are hermetic.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
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
            diagnostics_history TEXT,
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
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            payload_json TEXT,
            ts TEXT NOT NULL
        )"""
    )
    return conn


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _seed_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: str = "CID-1",
    env: str = "TEST",
    run_id: str = "run-discovery",
    gate_run_id: str | None = "run-discovery",
    target_floor: int = 5,
    status: str = "running",
    retry_count: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO products (sku,name,created_at,tags_json) VALUES (?,?,?,?)",
        ("SKU-1", "Widget", _now(), "[]"),
    )
    conn.execute(
        """INSERT INTO product_campaigns
             (sku, campaign_id, env, run_id, test_mode_to, started_at,
              started_by_user_id, status, target_floor,
              baseline_candidate_count, retry_count, gate_run_id)
           VALUES (?,?,?,?,?,?,?, ?, ?, ?, ?, ?)""",
        ("SKU-1", campaign_id, env, run_id, "op@console.app", _now(),
         1, status, target_floor, 0, retry_count, gate_run_id),
    )
    if run_id:
        conn.execute(
            """INSERT INTO product_campaign_runs
                 (campaign_id, env, run_id, kind, session_id, started_at)
               VALUES (?,?,?,?,?,?)""",
            (campaign_id, env, run_id, "outreach",
             f"kol-campaign:{env}:{campaign_id}", _now()),
        )


def _build_app(conn: sqlite3.Connection, bridge: _StubBridge,
               gateway: _StubGateway, *, role: str = "owner") -> FastAPI:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    app.include_router(products_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_bridge] = lambda: bridge
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[current_user] = lambda: {
        "id": 1, "email": f"{role}@console.app", "role": role, "is_active": 1,
    }
    return app


# ---------------------------------------------------------------------------
# Approve guard
# ---------------------------------------------------------------------------


def test_approve_shortlist_409_while_gate_run_id_set() -> None:
    """gate_run_id present + corresponding run still running ⇒ 409
    campaign_run_in_flight. Mirrors the scenario in the bug report:
    operator triggered rediscover, auto-retry is going, user clicks
    Approve on round-1 KOLs.
    """
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id="run-discovery")
    bridge = _StubBridge()
    bridge.candidates = [{"identity_id": 100, "primary_handle": "alice"}]
    bridge.identity_map[100] = {"primary_handle": "alice"}
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "running"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"]},
    )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "campaign_run_in_flight"
    # And the approve must NOT have started an outreach run.
    assert gateway.runs_started == []
    assert bridge.approve_calls == []


def test_approve_shortlist_409_during_brief_gate_eval_window() -> None:
    """The discovery run reached terminal but the gate has not yet
    cleared ``gate_run_id``. ``gate_active`` is still true; approve
    must stay blocked.
    """
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id="run-discovery",
                   status="running")
    bridge = _StubBridge()
    bridge.candidates = [{"identity_id": 100, "primary_handle": "alice"}]
    bridge.identity_map[100] = {"primary_handle": "alice"}
    gateway = _StubGateway()
    # Discovery run reached terminal but gate_run_id is still set.
    gateway.states["run-discovery"] = {"status": "completed"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"]},
    )

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "campaign_run_in_flight"


def test_approve_shortlist_succeeds_when_gate_cleared() -> None:
    """gate_run_id is None (gate already evaluated + cleared). Approve
    should proceed end-to-end and spawn an outreach run.
    """
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id=None,
                   status="closed")
    bridge = _StubBridge()
    bridge.candidates = [{"identity_id": 100, "primary_handle": "alice"}]
    bridge.identity_map[100] = {"primary_handle": "alice"}
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"]},
    )

    assert r.status_code == 200, r.text
    assert len(gateway.runs_started) == 1
    assert len(bridge.approve_calls) == 1

    # Approve overwrites ``run_id`` for display but MUST NOT touch
    # ``gate_run_id`` — it stays None.
    row = conn.execute(
        "SELECT run_id, gate_run_id, status FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["run_id"] == gateway.runs_started[0]["run_id"]
    assert row["gate_run_id"] is None
    assert row["status"] == "running"


def test_approve_shortlist_dedup_blocks_double_click() -> None:
    """Second approve within INFLIGHT_TTL_SECONDS → 409 approve_inflight."""
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id=None,
                   status="closed")
    bridge = _StubBridge()
    bridge.candidates = [{"identity_id": 100, "primary_handle": "alice"}]
    bridge.identity_map[100] = {"primary_handle": "alice"}
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed"}
    app = _build_app(conn, bridge, gateway)

    first = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"]},
    )
    assert first.status_code == 200, first.text

    # The first approve set status='running' on the new outreach run;
    # that's still in_flight, so the second approve hits the
    # campaign_run_in_flight branch BEFORE the dedup branch. Make the
    # outreach run terminal so we exercise the dedup path specifically.
    new_run_id = gateway.runs_started[0]["run_id"]
    gateway.states[new_run_id] = {"status": "completed"}
    # And clear status so the in-flight check passes.
    conn.execute(
        "UPDATE product_campaigns SET status='closed' WHERE campaign_id='CID-1'"
    )

    second = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"]},
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "approve_inflight"


# ---------------------------------------------------------------------------
# Rediscover guard
# ---------------------------------------------------------------------------


def test_rediscover_409_while_gate_active_even_if_run_terminal() -> None:
    """Even after the discovery run reaches terminal, the campaign is
    semantically "gate active" until ``evaluate_gate_after_terminal``
    clears ``gate_run_id``. The /rediscover endpoint must respect this.
    """
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id="run-discovery",
                   status="closed")
    bridge = _StubBridge()
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/rediscover",
        json={"env": "TEST", "additional_count": 3},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "campaign_run_in_flight"
    assert gateway.runs_started == []


# ---------------------------------------------------------------------------
# _sync_run_states + gate dispatch
# ---------------------------------------------------------------------------


def test_sync_run_states_does_not_fire_gate_on_approve_run_terminal() -> None:
    """Setup: discovery run already completed and gate cleared. An
    approve-driven outreach run is now in flight. When THAT outreach run
    reaches terminal, the discovery gate must NOT fire (target_floor is
    still set on the row, but ``gate_run_id`` is None).

    Without the fix, the old ``_sync_run_states`` polled
    ``product_campaigns.run_id`` and enqueued gate work on every
    terminal flip — causing spurious auto-retries after approve.
    """
    conn = _seed_conn()
    # Approve already ran and overwrote run_id. gate_run_id is None.
    _seed_campaign(
        conn,
        run_id="run-outreach",
        gate_run_id=None,
        target_floor=5,
        status="running",
        retry_count=0,
    )
    # Outreach run is now terminal.
    bridge = _StubBridge()
    gateway = _StubGateway()
    gateway.states["run-outreach"] = {"status": "completed"}

    app = _build_app(conn, bridge, gateway)

    # GET /products/SKU-1/campaigns runs _sync_run_states.
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # No new run should have been started (no auto-retry).
    assert gateway.runs_started == []

    # Row should be flipped to closed.
    row = conn.execute(
        "SELECT status, gate_run_id FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["status"] == "closed"
    assert row["gate_run_id"] is None


def test_sync_run_states_fires_gate_on_discovery_terminal() -> None:
    """When the DISCOVERY run terminates and current_visible_count <
    target_floor, gate fires an auto-retry. The new run's id should
    overwrite ``gate_run_id``.
    """
    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=5,
        status="running",
        retry_count=0,
    )
    bridge = _StubBridge()
    # current_visible = 2 (below floor of 5).
    bridge.candidates = [
        {"identity_id": 100, "primary_handle": "alice",
         "candidate_status": "discovered"},
        {"identity_id": 101, "primary_handle": "bob",
         "candidate_status": "discovered"},
    ]
    bridge.identity_map[100] = {"primary_handle": "alice"}
    bridge.identity_map[101] = {"primary_handle": "bob"}
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed", "output": ""}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # Gate fired an auto-retry.
    assert len(gateway.runs_started) == 1
    auto_retry_id = gateway.runs_started[0]["run_id"]
    row = conn.execute(
        "SELECT gate_run_id, retry_count FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] == auto_retry_id
    assert row["retry_count"] == 1


def test_gate_does_not_fire_when_approval_reduced_uncontacted_pool() -> None:
    """Regression: approving KOLs mid-rediscover used to depress the
    uncontacted-pool metric below ``target_floor``, triggering a
    spurious auto-retry. New gate semantics use the visible-pool
    metric, which is approval-immune — selected_for_outreach rows
    count toward ``current``.
    """
    conn = _seed_conn()
    # Operator asked for 3 more on top of a baseline of 2 → floor = 5.
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=5,
        status="running",
        retry_count=0,
    )
    bridge = _StubBridge()
    # Discovery agent added 3 new candidates. Operator then approved 2
    # of them (now selected_for_outreach). Pool size still = 5; visible
    # count = 5. Old metric (uncontacted) = 3, would have failed floor.
    bridge.candidates = [
        {"identity_id": 1, "primary_handle": "a",
         "candidate_status": "selected_for_outreach"},
        {"identity_id": 2, "primary_handle": "b",
         "candidate_status": "selected_for_outreach"},
        {"identity_id": 3, "primary_handle": "c",
         "candidate_status": "discovered"},
        {"identity_id": 4, "primary_handle": "d",
         "candidate_status": "discovered"},
        {"identity_id": 5, "primary_handle": "e",
         "candidate_status": "discovered"},
    ]
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed", "output": ""}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # Floor met → no auto-retry fired.
    assert gateway.runs_started == []
    # gate_run_id cleared.
    row = conn.execute(
        "SELECT gate_run_id FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] is None


def test_close_clears_gate_run_id() -> None:
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id="run-discovery")
    bridge = _StubBridge()
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "running"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/close?env=TEST",
        json={"status": "cancelled"},
    )
    assert r.status_code == 200, r.text
    row = conn.execute(
        "SELECT status, gate_run_id FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["status"] == "cancelled"
    assert row["gate_run_id"] is None


# ---------------------------------------------------------------------------
# Gateway eviction recovery — gateway evicts its in-memory run record ~1h
# after terminal, so a once-known run_id can return None on the next
# /campaigns GET. Without explicit handling, gate_run_id sticks forever and
# the operator is locked out (Approve disabled + Rediscover button gated).
# ---------------------------------------------------------------------------


def test_sync_gate_unsticks_when_run_evicted_floor_met() -> None:
    """Gateway returns None for ``gate_run_id`` (run evicted) AND current
    visible candidates already meet ``target_floor``: gate_run_id is
    cleared on the next GET. No auto-retry is fired.
    """
    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=3,
        status="running",
        retry_count=2,
    )
    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": 1, "primary_handle": "a",
         "candidate_status": "selected_for_outreach"},
        {"identity_id": 2, "primary_handle": "b",
         "candidate_status": "discovered"},
        {"identity_id": 3, "primary_handle": "c",
         "candidate_status": "discovered"},
    ]
    gateway = _StubGateway()
    # No entry for "run-discovery" in gateway.states → get_run returns None.

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # No auto-retry — floor was already met when we re-checked.
    assert gateway.runs_started == []
    row = conn.execute(
        "SELECT gate_run_id, status FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] is None
    # Row status also flipped to closed because the row.run_id was evicted
    # too (step-1 handling). gate_active=false → UI unblocks Approve.
    assert row["status"] == "closed"
    # The registered run row gets an ended_at on eviction.
    run_row = conn.execute(
        "SELECT ended_at FROM product_campaign_runs WHERE run_id='run-discovery'"
    ).fetchone()
    assert run_row["ended_at"] is not None


def test_sync_gate_auto_retries_when_run_evicted_floor_unmet() -> None:
    """Gateway returns None, retry_count < MAX, current < target_floor:
    gate evaluator fires an auto-retry (same outcome as the
    observed-terminal-with-floor-unmet path).
    """
    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=5,
        status="running",
        retry_count=1,
    )
    bridge = _StubBridge()
    # current_visible = 2 (below floor of 5).
    bridge.candidates = [
        {"identity_id": 100, "primary_handle": "alice",
         "candidate_status": "discovered"},
        {"identity_id": 101, "primary_handle": "bob",
         "candidate_status": "discovered"},
    ]
    gateway = _StubGateway()
    # No entry for "run-discovery" in gateway.states → evicted.

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    assert len(gateway.runs_started) == 1, "expected one auto-retry fired"
    new_run_id = gateway.runs_started[0]["run_id"]
    row = conn.execute(
        "SELECT gate_run_id, retry_count FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] == new_run_id
    assert row["retry_count"] == 2


def test_sync_gate_escalates_when_run_evicted_max_retries() -> None:
    """Gateway returns None, retry_count == MAX, current < target_floor:
    gate evaluator opens a ``discovery_floor_unmet`` escalation and
    clears gate_run_id (no further auto-retry).
    """
    from app.discovery_gate import MAX_AUTO_RETRIES

    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=10,
        status="running",
        retry_count=MAX_AUTO_RETRIES,
    )
    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": 1, "primary_handle": "a",
         "candidate_status": "discovered"},
    ]
    gateway = _StubGateway()
    # Evicted.

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # No further auto-retries.
    assert gateway.runs_started == []
    # Escalation opened.
    assert len(bridge.escalations) == 1
    assert bridge.escalations[0]["reason"] == "discovery_floor_unmet"
    # gate_run_id cleared so UI unblocks.
    row = conn.execute(
        "SELECT gate_run_id FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] is None


def test_sync_row_status_flips_to_closed_when_run_id_evicted() -> None:
    """Row has ``status='running'`` and a ``run_id`` the gateway has
    evicted (returns None). Status flips to ``closed`` so /start and the
    UI don't see a phantom-running campaign forever. ``gate_run_id`` is
    NULL here so the gate path is not exercised.
    """
    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id=None,
        status="running",
    )
    bridge = _StubBridge()
    gateway = _StubGateway()
    # Evicted — no entry in states.

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    row = conn.execute(
        "SELECT status FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["status"] == "closed"
