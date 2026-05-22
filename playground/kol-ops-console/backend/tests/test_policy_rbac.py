"""Phase D — RBAC matrix for ``/policies/{scope}``.

The bridge owns version storage; the console enforces the
owner / operator / viewer access rules from plan.md Phase E2.
Plan asks for: owner reads all + writes all; operator reads
company/escalation + own user_style, writes own user_style only;
viewer reads everything but writes nothing.

Stubs ``get_bridge`` so we can assert the *console* allows/denies the
request before it would hit the bridge. The deps overrides also pin
``current_user`` to a fixed role for each test, sidestepping the JWT
plumbing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_bridge  # noqa: E402
from app.routers import policies as policies_router  # noqa: E402


class _StubBridge:
    """In-process stub for BridgeClient. Returns deterministic payloads."""

    def __init__(self) -> None:
        self.get_calls: list[tuple[str, int | None]] = []
        self.put_calls: list[tuple[str, dict]] = []

    async def get_policy(self, scope: str, *, owner_user_id: int | None = None):
        self.get_calls.append((scope, owner_user_id))
        return {"policy": {"scope": scope, "owner_user_id": owner_user_id,
                            "content_md": "(stub)", "version": 1,
                            "is_active": 1}}

    async def put_policy(self, scope: str, body: dict):
        self.put_calls.append((scope, body))
        return {"ok": True, "scope": scope, "version": 2}


def _build_app(role: str, user_id: int = 7) -> tuple[FastAPI, _StubBridge]:
    """Construct a minimal FastAPI app that mounts only the policies
    router with deps overridden for the role under test.
    """
    app = FastAPI()
    app.include_router(policies_router.router)
    stub = _StubBridge()
    app.dependency_overrides[get_bridge] = lambda: stub
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id, "email": f"{role}@console.app", "role": role,
        "is_active": 1,
    }
    return app, stub


# -- READ MATRIX -----------------------------------------------------------

@pytest.mark.parametrize("role", ["owner", "operator", "viewer"])
@pytest.mark.parametrize("scope", ["company_style", "escalation_rules"])
def test_read_global_scopes_allowed_for_all_roles(role: str, scope: str):
    """Per plan E2: ``company_style`` and ``escalation_rules`` are
    readable by every authenticated user (owner / operator / viewer).
    """
    app, _ = _build_app(role)
    r = TestClient(app).get(f"/policies/{scope}")
    assert r.status_code == 200, r.text


def test_read_user_style_self_allowed():
    """Operator can read their own user_style."""
    app, _ = _build_app("operator", user_id=7)
    r = TestClient(app).get("/policies/user_style?owner_user_id=7")
    assert r.status_code == 200


def test_read_user_style_someone_else_denied_for_operator():
    """Operator must NOT be able to read someone else's user_style."""
    app, _ = _build_app("operator", user_id=7)
    r = TestClient(app).get("/policies/user_style?owner_user_id=99")
    assert r.status_code == 403


def test_owner_reads_any_user_style():
    """Owner is allowed to read any operator's user_style."""
    app, _ = _build_app("owner", user_id=1)
    r = TestClient(app).get("/policies/user_style?owner_user_id=42")
    assert r.status_code == 200


# -- WRITE MATRIX ----------------------------------------------------------

@pytest.mark.parametrize("scope", ["company_style", "escalation_rules"])
def test_owner_writes_global_scopes(scope: str):
    app, stub = _build_app("owner")
    r = TestClient(app).put(
        f"/policies/{scope}",
        json={"content_md": "new content"},
    )
    assert r.status_code == 200, r.text
    assert stub.put_calls[-1][0] == scope
    assert stub.put_calls[-1][1].get("content_md") == "new content"


@pytest.mark.parametrize("scope", ["company_style", "escalation_rules"])
def test_operator_cannot_write_global_scopes(scope: str):
    app, _ = _build_app("operator")
    r = TestClient(app).put(
        f"/policies/{scope}",
        json={"content_md": "operator tried to overwrite"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("scope", ["company_style", "escalation_rules"])
def test_viewer_cannot_write_global_scopes(scope: str):
    app, _ = _build_app("viewer")
    r = TestClient(app).put(
        f"/policies/{scope}",
        json={"content_md": "viewer tried"},
    )
    assert r.status_code == 403


def test_operator_writes_own_user_style():
    app, stub = _build_app("operator", user_id=7)
    r = TestClient(app).put(
        "/policies/user_style",
        json={"content_md": "my personal style", "owner_user_id": 7},
    )
    assert r.status_code == 200, r.text
    assert stub.put_calls[-1][0] == "user_style"


def test_operator_cannot_write_other_users_user_style():
    app, _ = _build_app("operator", user_id=7)
    r = TestClient(app).put(
        "/policies/user_style",
        json={"content_md": "trying to overwrite", "owner_user_id": 99},
    )
    assert r.status_code == 403


def test_viewer_cannot_write_user_style_even_own():
    app, _ = _build_app("viewer", user_id=7)
    r = TestClient(app).put(
        "/policies/user_style",
        json={"content_md": "viewer tried", "owner_user_id": 7},
    )
    assert r.status_code == 403


def test_owner_writes_other_users_user_style():
    """Owner is allowed to set any operator's user_style on their behalf."""
    app, stub = _build_app("owner", user_id=1)
    r = TestClient(app).put(
        "/policies/user_style",
        json={"content_md": "admin-managed", "owner_user_id": 42},
    )
    assert r.status_code == 200, r.text
    assert stub.put_calls[-1][0] == "user_style"
