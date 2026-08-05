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

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_bridge, get_conn, get_gateway  # noqa: E402
from app.routers import campaigns as campaigns_router  # noqa: E402
from app.routers import products as products_router  # noqa: E402


@pytest.fixture(autouse=True)
def _sync_run_states_on_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert GET-triggered gate dispatch (legacy sync path)."""
    from app.config import get_settings

    monkeypatch.setenv("KOC_RUN_RECONCILER_ENABLED", "false")
    monkeypatch.setenv("KOC_SYNC_RUN_STATES_ON_GET", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
        self.decision_learning_calls: list[dict[str, Any]] = []

    async def list_candidates(self, campaign_id: str, *, env: str):
        return list(self.candidates)

    async def get_identity(self, identity_id: int):
        return self.identity_map.get(identity_id, {})

    async def batch_identity_briefs(self, identity_ids: list[int]):
        return {
            iid: self.identity_map.get(iid, {"identity_id": iid})
            for iid in identity_ids
        }

    async def batch_creator_brief_status(
        self, identity_ids: list[int], *, env: str = "LIVE",
    ):
        return {
            iid: {"ready": False, "status": "missing", "missing_keys": []}
            for iid in identity_ids
        }

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

    async def get_lanes(self, campaign_id: str, *, env: str):
        return {"items": []}

    async def list_candidate_handles(self, campaign_id: str, *, env: str):
        return []

    async def open_escalation(self, body: dict):
        self.escalations.append(body)
        return {"ok": True}

    async def discovery_feedback_requirements(self, *, sku, env):
        # Past the early-learning phase: comment optional, tags still required.
        return {"comment_required": False}

    async def record_shortlist_decision(self, body: dict):
        self.decision_learning_calls.append(body)
        return {"recorded": len(body.get("decisions") or []), "event_ids": []}


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
                        session_id: str | None = None, model: str | None = None,
                        **kwargs: Any):
        new_id = self._mint_id()
        self.runs_started.append({
            "run_id": new_id,
            "input": input,
            "session_id": session_id,
        })
        # Default: newly started runs are "running".
        self.states[new_id] = {"status": "running"}
        return {"run_id": new_id, "status": "queued"}

    async def start_run_with_retry(self, **kwargs: Any) -> dict[str, Any]:
        return await self.start_run(**kwargs)

    async def get_run(self, run_id: str):
        return self.states.get(run_id)

    async def stop_run(self, run_id: str):
        if run_id in self.states:
            self.states[run_id]["status"] = "cancelled"
        return {"status": "stopping"}

    async def launch_via_queue(self, start_fn, **kwargs: Any):
        return await start_fn()

    def ensure_run_drained(self, run_id: str) -> None:
        return None


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
        json={"env": "TEST", "selected_handles": ["alice"], "decision_feedback": {"shared_tags": ["tone_match"], "shared_comment": "fits the brief"}},
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
        json={"env": "TEST", "selected_handles": ["alice"], "decision_feedback": {"shared_tags": ["tone_match"], "shared_comment": "fits the brief"}},
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
    bridge.identity_map[100] = {
        "primary_handle": "alice",
        "primary_email": "alice@example.com",
    }
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"], "decision_feedback": {"shared_tags": ["tone_match"], "shared_comment": "fits the brief"}},
    )

    assert r.status_code == 200, r.text
    assert len(gateway.runs_started) == 1
    assert len(bridge.route_calls) == 1

    # Approve overwrites ``run_id`` for display but MUST NOT touch
    # ``gate_run_id`` — it stays None.
    row = conn.execute(
        "SELECT run_id, gate_run_id, status FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["run_id"] == gateway.runs_started[0]["run_id"]
    assert row["gate_run_id"] is None
    assert row["status"] == "running"


def test_approve_shortlist_queues_email_discovery_when_missing_email() -> None:
    """Approved identities without primary_email get kol-email-discover runs."""
    conn = _seed_conn()
    _seed_campaign(conn, run_id="run-discovery", gate_run_id=None, status="closed")
    bridge = _StubBridge()
    bridge.candidates = [{"identity_id": 100, "primary_handle": "alice"}]
    bridge.identity_map[100] = {"primary_handle": "alice"}
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "completed"}
    app = _build_app(conn, bridge, gateway)

    r = TestClient(app).post(
        "/campaigns/CID-1/approve-shortlist",
        json={"env": "TEST", "selected_handles": ["alice"], "decision_feedback": {"shared_tags": ["tone_match"], "shared_comment": "fits the brief"}},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("email_discovery")
    assert len(gateway.runs_started) == 2
    sessions = [run["session_id"] for run in gateway.runs_started]
    email_sessions = [s for s in sessions if s and s.startswith("kol-email-discover:")]
    assert len(email_sessions) == 1
    # env:identity_id:run_token — one browser tab per gateway run
    assert email_sessions[0].count(":") >= 3
    assert any(s and s.startswith("kol-campaign-outreach:") for s in sessions)
    outreach = next(
        run for run in gateway.runs_started
        if (run.get("session_id") or "").startswith("kol-campaign-outreach:")
    )
    assert "email_discovery_queued" in outreach["input"]


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
        json={"env": "TEST", "selected_handles": ["alice"], "decision_feedback": {"shared_tags": ["tone_match"], "shared_comment": "fits the brief"}},
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
        json={"env": "TEST", "selected_handles": ["alice"], "decision_feedback": {"shared_tags": ["tone_match"], "shared_comment": "fits the brief"}},
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


def test_early_escalation_after_consecutive_zero_new() -> None:
    """Two consecutive auto-retry rounds produce zero net-new candidates:
    gate escalates immediately instead of firing a third auto-retry,
    even though retry_count < MAX_AUTO_RETRIES.

    Reproduces the SSF8033-20260609 2026-07-01 failure mode where 5 zero-new
    rounds burned ~5.2M tokens before escalation.
    """
    import json as _json

    from app.discovery_gate import MAX_CONSECUTIVE_ZERO_NEW_RUNS

    assert MAX_CONSECUTIVE_ZERO_NEW_RUNS == 2, (
        "test assumes the configured threshold is 2"
    )

    conn = _seed_conn()
    # retry_count=1 means this is the 2nd auto-retry; with the pre-seeded
    # diagnostics history below, the streak will reach 2 after the current
    # round appends its own zero-growth entry.
    _seed_campaign(
        conn,
        run_id="run-retry-2",
        gate_run_id="run-retry-2",
        target_floor=10,
        status="running",
        retry_count=1,
    )
    # Pre-seed diagnostics_history with two prior rounds both stuck at
    # persisted_count_at_end=3 (zero new between them). The current round
    # will also report 3 visible candidates, so the streak hits 2 and
    # early-escalation fires.
    prior_history = [
        {
            "round_index": 1,
            "run_id": "run-initial",
            "target_floor": 10,
            "persisted_count_at_end": 3,
            "is_auto_retry": False,
        },
        {
            "round_index": 2,
            "run_id": "run-retry-1",
            "target_floor": 10,
            "persisted_count_at_end": 3,
            "is_auto_retry": True,
        },
    ]
    conn.execute(
        "UPDATE product_campaigns SET diagnostics_history=? "
        "WHERE campaign_id='CID-1' AND env='TEST'",
        (_json.dumps(prior_history),),
    )

    bridge = _StubBridge()
    # 3 visible candidates — same as the prior two rounds → zero new.
    bridge.candidates = [
        {"identity_id": 1, "primary_handle": "a",
         "candidate_status": "discovered"},
        {"identity_id": 2, "primary_handle": "b",
         "candidate_status": "discovered"},
        {"identity_id": 3, "primary_handle": "c",
         "candidate_status": "discovered"},
    ]
    gateway = _StubGateway()

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # Early-escalation: NO auto-retry fired even though retry_count(1) <
    # MAX_AUTO_RETRIES(5).
    assert gateway.runs_started == [], (
        "early-escalation must not fire another auto-retry"
    )
    # Escalation opened with the zero-new reason.
    assert len(bridge.escalations) == 1
    esc = bridge.escalations[0]
    assert esc["reason"] == "discovery_floor_unmet"
    assert "0 新增" in esc["question_to_operator"]
    # gate_run_id cleared.
    row = conn.execute(
        "SELECT gate_run_id, floor_unmet_reason FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] is None
    assert row["floor_unmet_reason"] is not None


def test_no_early_escalation_when_latest_round_grew_pool() -> None:
    """Sanity guard: if the most recent round DID add candidates, the
    zero-new streak is broken and early-escalation must NOT fire — the
    gate falls through to the normal auto-retry path.
    """
    import json as _json

    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-retry-2",
        gate_run_id="run-retry-2",
        target_floor=10,
        status="running",
        retry_count=1,
    )
    # Prior rounds: 3 → 3 (one zero-new transition). Current round will
    # report 5 → streak breaks at 1 (< MAX_CONSECUTIVE_ZERO_NEW_RUNS=2).
    prior_history = [
        {"round_index": 1, "run_id": "run-initial",
         "persisted_count_at_end": 3, "is_auto_retry": False},
        {"round_index": 2, "run_id": "run-retry-1",
         "persisted_count_at_end": 3, "is_auto_retry": True},
    ]
    conn.execute(
        "UPDATE product_campaigns SET diagnostics_history=? "
        "WHERE campaign_id='CID-1' AND env='TEST'",
        (_json.dumps(prior_history),),
    )

    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": i, "primary_handle": f"h{i}",
         "candidate_status": "discovered"}
        for i in range(1, 6)  # 5 visible — grew from 3
    ]
    gateway = _StubGateway()

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    # Streak broken → normal auto-retry path, NOT early-escalation.
    assert len(bridge.escalations) == 0, (
        "no early-escalation when the latest round grew the pool"
    )
    assert len(gateway.runs_started) == 1, (
        "normal auto-retry should fire when streak < threshold"
    )


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


# ---------------------------------------------------------------------------
# Round-2 optimizations: /start mutex, auto-retry in-flight guard,
# precompress snapshot injection, hard_rules block, visited_handles recovery
# ---------------------------------------------------------------------------


def test_start_409_when_gate_run_id_set() -> None:
    """/start must return 409 campaign_run_in_flight when a discovery gate
    is active (gate_run_id set), mirroring /rediscover's mutex.
    """
    conn = _seed_conn()
    _seed_campaign(conn, campaign_id="CID-1", env="LIVE",
                   run_id="run-discovery", gate_run_id="run-discovery",
                   status="running")
    conn.execute(
        "UPDATE products SET url='https://example.com' WHERE sku='SKU-1'"
    )
    bridge = _StubBridge()
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {"status": "running"}

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).post(
        "/campaigns/CID-1/start?force=true",
        json={
            "env": "LIVE",
            "product_sku": "SKU-1",
            "product_display_name": "Widget",
            "headcount_target": 3,
            "budget_per_kol": 100.0,
            "budget_total": 1000.0,
            "absolute_floor": 5.0,
            "deliverable_platforms": ["instagram"],
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "campaign_run_in_flight"
    assert gateway.runs_started == []


def test_evaluate_gate_skips_auto_retry_when_in_flight() -> None:
    """When evaluate_gate_after_terminal detects an in-flight run (e.g.
    operator fired /rediscover between terminal sync and gate evaluation),
    it must skip the auto-retry instead of stacking a parallel run.
    """
    import asyncio

    from app.discovery_gate import evaluate_gate_after_terminal
    from app.bridge_agent_contract_loader import gateway_contract_for_brief

    conn = _seed_conn()
    _seed_campaign(
        conn, run_id="run-old", gate_run_id="run-old",
        target_floor=10, status="running", retry_count=0,
    )
    bridge = _StubBridge()
    bridge.candidates = []  # current=0 < target_floor=10
    gateway = _StubGateway()
    # The old discovery run is terminal...
    gateway.states["run-old"] = {"status": "completed"}
    # ...but the operator already fired a rediscover that is now running.
    gateway.states["run-operator"] = {"status": "running"}
    conn.execute(
        "UPDATE product_campaigns SET run_id='run-operator', "
        "gate_run_id='run-operator' WHERE campaign_id='CID-1' AND env='TEST'"
    )

    instructions = gateway_contract_for_brief(compact=True)
    result = asyncio.run(evaluate_gate_after_terminal(
        bridge=bridge, gateway=gateway, conn=conn,
        campaign_id="CID-1", env="TEST",
        target_floor=10, retry_count=0,
        run_info={"status": "completed", "output": ""},
        rediscovery_instructions=instructions,
        gate_run_id="run-old",  # stale — row now has run-operator
    ))
    # gate_run_id mismatch -> skipped_stale_gate_run_id (the in-flight guard
    # is a secondary defense; the primary stale check fires first here).
    assert result["ok"] is True
    assert result["outcome"] in (
        "skipped_stale_gate_run_id", "skipped_in_flight_on_terminal",
    )
    assert gateway.runs_started == []


def test_compose_rediscover_brief_includes_hard_rules() -> None:
    """The brief must inline the # hard_rules block so the agent sees the
    bootstrap / ingest-self-check / visit-conclusion rules even when its
    skill_view cache serves a stale SKILL.md.
    """
    from app.discovery_gate import _compose_rediscover_brief

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE products (sku TEXT, name TEXT, url TEXT, tags_json TEXT, "
        "notes TEXT, pitch_md TEXT, selling_points TEXT, variants_json TEXT)"
    )
    conn.execute(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?)",
        ("SKU-1", "Widget", "https://example.com", "[]", "", "", "", "[]"),
    )
    product = conn.execute("SELECT * FROM products WHERE sku='SKU-1'").fetchone()
    brief = _compose_rediscover_brief(
        campaign_id="CID-1", env="LIVE", product=product,
        additional_count=5, excluded_handles=["foo", "bar"],
        test_mode_to=None, prior_diagnostics=None,
    )
    assert "# hard_rules" in brief
    assert "--summary" in brief
    assert "rpa_precheck_handle" in brief
    assert "voice_descriptors" in brief
    assert "visited_handles" in brief
    conn.close()


def test_compose_rediscover_brief_injects_precompress_snapshot(
    tmp_path, monkeypatch,
) -> None:
    """When a precompress snapshot exists on disk, its handles are merged
    into # resume_directives even without prior_diagnostics.
    """
    import json as _json
    import tempfile

    from app.discovery_gate import _compose_rediscover_brief

    sid = "kol-campaign:LIVE:CID-1"
    safe = sid.replace(":", "_")
    snap = tmp_path / f"precompress_pending_{safe}.json"
    snap.write_text(_json.dumps({
        "session_id": sid,
        "pending_handles": ["snap_handle1", "snap_handle2"],
    }))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE products (sku TEXT, name TEXT, url TEXT, tags_json TEXT, "
        "notes TEXT, pitch_md TEXT, selling_points TEXT, variants_json TEXT)"
    )
    conn.execute(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?)",
        ("SKU-1", "Widget", "", "[]", "", "", "", "[]"),
    )
    product = conn.execute("SELECT * FROM products WHERE sku='SKU-1'").fetchone()
    brief = _compose_rediscover_brief(
        campaign_id="CID-1", env="LIVE", product=product,
        additional_count=3, excluded_handles=[],
        test_mode_to=None, prior_diagnostics=None,
    )
    assert "# resume_directives" in brief
    assert "snap_handle1" in brief
    assert "snap_handle2" in brief
    conn.close()


def test_extract_visited_handles_heuristic_recovers_from_prose() -> None:
    """When the agent omits visited_handles YAML, the heuristic should
    recover handles mentioned near DISCARD / ingest signals.
    """
    from app.discovery_gate import _extract_visited_handles_heuristic

    text = (
        "I visited @building_a_barndo but it's below 100K - DISCARD.\n"
        "Successfully ingested `carson.roney` (candidate_id 485).\n"
        "Also checked @vda_designs."
    )
    result = _extract_visited_handles_heuristic(text)
    assert result is not None
    handles = {item.split(" — ")[0] for item in result}
    assert "building_a_barndo" in handles
    assert "carson.roney" in handles
    assert "vda_designs" in handles


_PREMATURE_OUTPUT = (
    "Bootstrap completed.\n"
    "STEP_0 result: cationz requires profile verification before any "
    "new discovery. The other four pending-ingest entries are already in CAL."
)


def test_premature_bootstrap_stop_fires_recovery_auto_retry() -> None:
    """Completed run with empty-shell soft-stop below floor → recovery retry."""
    import json as _json

    from app.discovery_gate import EXIT_KIND_PREMATURE, PREMATURE_FLOOR_REASON

    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=10,
        status="running",
        retry_count=0,
    )
    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": i, "primary_handle": f"h{i}",
         "candidate_status": "discovered"}
        for i in range(1, 4)
    ]
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {
        "status": "completed",
        "output": _PREMATURE_OUTPUT,
    }

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    assert len(gateway.runs_started) == 1
    assert "# premature_exit_recovery (HARD)" in gateway.runs_started[0]["input"]
    row = conn.execute(
        "SELECT gate_run_id, retry_count, status, floor_unmet_reason, "
        "diagnostics_history FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["status"] == "running"
    assert row["retry_count"] == 1
    assert row["gate_run_id"] == gateway.runs_started[0]["run_id"]
    assert row["floor_unmet_reason"] == PREMATURE_FLOOR_REASON
    hist = _json.loads(row["diagnostics_history"] or "[]")
    assert hist[-1].get("exit_kind") == EXIT_KIND_PREMATURE


def test_premature_exit_repeated_escalates_after_cap() -> None:
    """Third premature round escalates; no further auto-retry."""
    import json as _json

    from app.discovery_gate import (
        EXIT_KIND_PREMATURE,
        MAX_PREMATURE_EXIT_RECOVERIES,
        PREMATURE_FLOOR_REASON,
    )

    assert MAX_PREMATURE_EXIT_RECOVERIES == 2
    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=10,
        status="running",
        retry_count=2,
    )
    prior = [
        {
            "round_index": 1,
            "persisted_count_at_end": 3,
            "exit_kind": EXIT_KIND_PREMATURE,
            "floor_unmet_reason": PREMATURE_FLOOR_REASON,
        },
        {
            "round_index": 2,
            "persisted_count_at_end": 3,
            "exit_kind": EXIT_KIND_PREMATURE,
            "floor_unmet_reason": PREMATURE_FLOOR_REASON,
        },
    ]
    conn.execute(
        "UPDATE product_campaigns SET diagnostics_history=? "
        "WHERE campaign_id='CID-1' AND env='TEST'",
        (_json.dumps(prior),),
    )
    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": i, "primary_handle": f"h{i}",
         "candidate_status": "discovered"}
        for i in range(1, 4)
    ]
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {
        "status": "completed",
        "output": _PREMATURE_OUTPUT,
    }

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    assert gateway.runs_started == []
    assert len(bridge.escalations) == 1
    esc = bridge.escalations[0]
    assert esc["reason"] == "discovery_floor_unmet"
    assert "早停" in esc["question_to_operator"]
    assert "niche 枯竭" in esc["question_to_operator"]
    row = conn.execute(
        "SELECT gate_run_id, floor_unmet_reason FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] is None
    assert row["floor_unmet_reason"] == "premature_exit_repeated"


def test_premature_under_cap_skips_zero_new_early_escalation() -> None:
    """Real zero-new history + current premature under cap → recovery retry,
    not niche early-escalation.
    """
    import json as _json

    from app.discovery_gate import EXIT_KIND_PREMATURE

    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=10,
        status="running",
        retry_count=1,
    )
    # Two real zero-new rounds — would early-escalate if current were also
    # counted as a normal zero-new round.
    prior = [
        {"round_index": 1, "persisted_count_at_end": 3, "is_auto_retry": False},
        {"round_index": 2, "persisted_count_at_end": 3, "is_auto_retry": True},
    ]
    conn.execute(
        "UPDATE product_campaigns SET diagnostics_history=? "
        "WHERE campaign_id='CID-1' AND env='TEST'",
        (_json.dumps(prior),),
    )
    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": i, "primary_handle": f"h{i}",
         "candidate_status": "discovered"}
        for i in range(1, 4)
    ]
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {
        "status": "completed",
        "output": _PREMATURE_OUTPUT,
    }

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    assert len(bridge.escalations) == 0
    assert len(gateway.runs_started) == 1
    hist = _json.loads(
        conn.execute(
            "SELECT diagnostics_history FROM product_campaigns "
            "WHERE campaign_id='CID-1' AND env='TEST'"
        ).fetchone()[0]
    )
    assert hist[-1].get("exit_kind") == EXIT_KIND_PREMATURE


def test_floor_met_with_empty_diagnostics_no_premature_recovery() -> None:
    conn = _seed_conn()
    _seed_campaign(
        conn,
        run_id="run-discovery",
        gate_run_id="run-discovery",
        target_floor=3,
        status="running",
        retry_count=0,
    )
    bridge = _StubBridge()
    bridge.candidates = [
        {"identity_id": i, "primary_handle": f"h{i}",
         "candidate_status": "discovered"}
        for i in range(1, 4)
    ]
    gateway = _StubGateway()
    gateway.states["run-discovery"] = {
        "status": "completed",
        "output": _PREMATURE_OUTPUT,
    }

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    assert gateway.runs_started == []
    assert bridge.escalations == []
    row = conn.execute(
        "SELECT gate_run_id FROM product_campaigns "
        "WHERE campaign_id='CID-1' AND env='TEST'"
    ).fetchone()
    assert row["gate_run_id"] is None


def test_premature_at_max_auto_retries_escalates() -> None:
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
    gateway.states["run-discovery"] = {
        "status": "completed",
        "output": _PREMATURE_OUTPUT,
    }

    app = _build_app(conn, bridge, gateway)
    r = TestClient(app).get("/products/SKU-1/campaigns?env=TEST")
    assert r.status_code == 200, r.text

    assert gateway.runs_started == []
    assert len(bridge.escalations) == 1
    assert "premature_exit_repeated" in (
        conn.execute(
            "SELECT floor_unmet_reason FROM product_campaigns "
            "WHERE campaign_id='CID-1' AND env='TEST'"
        ).fetchone()[0]
        or ""
    )
