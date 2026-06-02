"""Regression tests for ``POST /products`` variant auto-ingest.

The product form can pre-detect variants, but backend must still enforce
auto-ingest from URL so products are consistently saved with full specs even
when the frontend skips "Detect variants".
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_conn  # noqa: E402
from app.routers import products as products_router  # noqa: E402


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
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


def _build_client(conn: sqlite3.Connection) -> TestClient:
    app = FastAPI()
    app.include_router(products_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "email": "owner@console.app",
        "role": "owner",
        "is_active": 1,
    }
    return TestClient(app)


def test_post_products_auto_ingests_url_variants_and_merges_manual(monkeypatch):
    conn = _seed_conn()
    client = _build_client(conn)

    monkeypatch.setattr(
        products_router,
        "parse_variants_from_url",
        lambda _: [
            {
                "id": "35590",
                "label": "2 Armless Chair+1 Armrest / Suede Fabric / Beige · SF8181E265",
                "url": "https://www.povison.com/foo.html?variant=35590",
                "attributes": {
                    "size": "2 Armless Chair+1 Armrest",
                    "material": "Suede Fabric",
                    "color": "Beige",
                },
            },
            {
                "id": "37384",
                "label": "2 Armless Chair+2 Armrest / Suede Fabric / Green · SF8181G265",
                "url": "https://www.povison.com/foo.html?variant=37384",
                "attributes": {
                    "size": "2 Armless Chair+2 Armrest",
                    "material": "Suede Fabric",
                    "color": "Green",
                },
            },
        ],
    )

    resp = client.post(
        "/products",
        json={
            "sku": "POVISON-SAILBOAT",
            "name": "Sailboat Sofa",
            "url": "https://www.povison.com/foo.html?variant=37384",
            "tags": ["sofa"],
            "notes": None,
            "selling_points": "deep seat",
            "pitch_md": None,
            # Manual variant overlaps with auto row id=37384; backend should
            # dedupe by id and preserve manual row order precedence.
            "variants": [
                {
                    "id": "37384",
                    "label": "manual green row",
                    "url": "https://www.povison.com/foo.html?variant=37384",
                    "attributes": {"color": "Green"},
                }
            ],
            "default_budget_per_kol": None,
            "default_budget_total": None,
            "default_absolute_floor": None,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["ok"] is True

    detail = client.get("/products/POVISON-SAILBOAT")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [v["id"] for v in body["variants"]] == ["37384", "35590"]
    assert body["variants"][0]["label"] == "manual green row"
    assert body["variants"][1]["url"] == "https://www.povison.com/foo.html?variant=35590"


def test_post_products_rejects_missing_or_invalid_url():
    conn = _seed_conn()
    client = _build_client(conn)

    missing = client.post(
        "/products",
        json={
            "sku": "SKU-NO-URL",
            "name": "No URL Sofa",
            "tags": [],
            "variants": [],
        },
    )
    assert missing.status_code == 422, missing.text

    invalid = client.post(
        "/products",
        json={
            "sku": "SKU-BAD-URL",
            "name": "Bad URL Sofa",
            "url": "not-a-url",
            "tags": [],
            "variants": [],
        },
    )
    assert invalid.status_code == 422, invalid.text


def test_parse_variants_rejects_invalid_url():
    conn = _seed_conn()
    client = _build_client(conn)

    r = client.post("/products/parse-variants", json={"url": "broken-url"})
    assert r.status_code == 422, r.text
