"""Router tests for ``/gateway-approvals``.

Covers:
* GET returns whatever the watcher has captured in its in-memory state.
* POST proxies to the gateway with the choice and writes an audit row.
* RBAC: only owner / operator can resolve; viewer is forbidden.
* Upstream 409 surfaces as 409 (so the FE can drop the entry).
* Upstream 502 surfaces as 502.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_conn, get_gateway  # noqa: E402
from app.gateway_approval_watcher import watcher  # noqa: E402
from app.gateway_client import GatewayError  # noqa: E402
from app.routers import gateway_approvals  # noqa: E402


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
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


class _FakeGateway:
    def __init__(self, *, raise_with: GatewayError | None = None,
                 response: dict[str, Any] | None = None) -> None:
        self._raise_with = raise_with
        self._response = response or {
            "object": "hermes.run.approval_response",
            "run_id": "r1",
            "choice": "deny",
            "resolved": 1,
        }
        self.calls: list[tuple[str, str]] = []

    async def resolve_approval(self, run_id: str, *, choice: str) -> dict[str, Any]:
        self.calls.append((run_id, choice))
        if self._raise_with is not None:
            raise self._raise_with
        return self._response


def _build_app(*, role: str = "operator", gateway: _FakeGateway | None = None):
    app = FastAPI()
    app.include_router(gateway_approvals.router)
    conn = _seed_conn()
    gw = gateway or _FakeGateway()
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_gateway] = lambda: gw
    app.dependency_overrides[current_user] = lambda: {
        "id": 1, "email": "op@console", "role": role, "is_active": 1,
    }
    return app, conn, gw


@pytest.fixture(autouse=True)
def _clear_watcher_state():
    # Each test starts with a clean watcher snapshot.
    watcher._pending.clear()
    watcher._seq = 0
    yield
    watcher._pending.clear()
    watcher._seq = 0


def test_list_returns_snapshot_and_seq() -> None:
    watcher._open(
        run_id="r1", kind="outreach", campaign_id="CID-1",
        payload={"command": "rm -rf /", "description": "danger"},
    )
    app, _, _ = _build_app()
    client = TestClient(app)
    r = client.get("/gateway-approvals")
    assert r.status_code == 200
    body = r.json()
    assert body["seq"] == 1
    approvals = body["approvals"]
    assert len(approvals) == 1
    assert approvals[0]["run_id"] == "r1"
    assert approvals[0]["campaign_id"] == "CID-1"
    assert approvals[0]["source"] == "gateway"
    assert approvals[0]["choices"] == ["once", "session", "always", "deny"]


def test_resolve_proxies_choice_and_writes_audit() -> None:
    app, conn, gw = _build_app()
    client = TestClient(app)
    r = client.post(
        "/gateway-approvals/r1/resolve",
        json={"choice": "deny", "note": "blocked"},
    )
    assert r.status_code == 200
    assert gw.calls == [("r1", "deny")]
    rows = list(conn.execute("SELECT action, target, payload_json FROM audit_log"))
    assert len(rows) == 1
    assert rows[0]["action"] == "gateway_approval.resolve"
    assert rows[0]["target"] == "r1"
    assert '"deny"' in rows[0]["payload_json"]


def test_resolve_rejects_unknown_choice() -> None:
    app, _, _ = _build_app()
    client = TestClient(app)
    r = client.post("/gateway-approvals/r1/resolve", json={"choice": "maybe"})
    assert r.status_code == 422


def test_resolve_forbidden_for_viewer() -> None:
    app, _, gw = _build_app(role="viewer")
    client = TestClient(app)
    r = client.post("/gateway-approvals/r1/resolve", json={"choice": "once"})
    assert r.status_code == 403
    assert gw.calls == []  # gateway never called


def test_resolve_passes_through_upstream_409() -> None:
    """A 409 from the upstream gateway means the approval was already
    cleared; the FE wants to drop the row silently rather than retry.
    """
    gw = _FakeGateway(raise_with=GatewayError(409, "approval_not_pending"))
    app, _, _ = _build_app(gateway=gw)
    client = TestClient(app)
    r = client.post("/gateway-approvals/r1/resolve", json={"choice": "deny"})
    assert r.status_code == 409


def test_resolve_maps_transport_error_to_502() -> None:
    gw = _FakeGateway(raise_with=GatewayError(502, "gateway unreachable"))
    app, _, _ = _build_app(gateway=gw)
    client = TestClient(app)
    r = client.post("/gateway-approvals/r1/resolve", json={"choice": "once"})
    assert r.status_code == 502
