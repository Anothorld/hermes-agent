"""Step 4 — Bridge reviews endpoint tests (t41) + agent prompt (t42).

Uses FastAPI TestClient with the povison_reviews module patched so no DB is
hit. Asserts the JSON contract + graceful ok=False when not configured.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# t41 — endpoint contract
# ---------------------------------------------------------------------------
def test_t41_health_ok_when_configured(client, monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    r = client.get("/api/povison-reviews/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["configured"] is True


def test_t41_health_false_when_not_configured(client, monkeypatch):
    for k in ("MAGENTO_DB_HOST", "MAGENTO_DB_PASS"):
        monkeypatch.delenv(k, raising=False)
    r = client.get("/api/povison-reviews/health")
    assert r.status_code == 200  # never 500
    body = r.json()
    assert body["ok"] is False and body["configured"] is False


def test_t41_by_spu_returns_reviews(client, monkeypatch):
    """When the module returns reviews, the endpoint returns ok=True + the list."""
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    fake = [
        {"reviewId": 1, "nickname": "Megan", "date": "2025-10-17", "title": "t",
         "detail": "d", "rating": 5, "helpfulCount": 3, "sourceType": "ORDER"},
    ]
    with patch("povison_reviews.fetch_reviews", return_value=fake):
        r = client.get("/api/povison-reviews/by-spu", params={"spu": 42, "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["spu"] == "42"
    assert body["count"] == 1
    assert body["reviews"][0]["nickname"] == "Megan"


def test_t41_by_spu_ok_false_when_not_configured(client, monkeypatch):
    """Not configured → fetch_reviews returns [] → ok=False, not a 500."""
    for k in ("MAGENTO_DB_HOST", "MAGENTO_DB_PASS"):
        monkeypatch.delenv(k, raising=False)
    r = client.get("/api/povison-reviews/by-spu", params={"spu": 42})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["count"] == 0
    assert body["reviews"] == []


def test_t41_by_spu_requires_spu(client):
    """Missing spu → FastAPI returns 422 (query-param validation), not 200."""
    r = client.get("/api/povison-reviews/by-spu")
    assert r.status_code in (400, 422)


def test_t41_summary_returns_aggregate(client, monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    agg = {"reviewsCount": 23, "ratingSummary": 92, "rating": 4}
    with patch("povison_reviews.fetch_summary", return_value=agg):
        r = client.get("/api/povison-reviews/summary", params={"spu": 42})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["reviewsCount"] == 23
    assert body["rating"] == 4


def test_t41_summary_ok_false_when_no_reviews(client, monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    agg = {"reviewsCount": 0, "ratingSummary": 0, "rating": None}
    with patch("povison_reviews.fetch_summary", return_value=agg):
        r = client.get("/api/povison-reviews/summary", params={"spu": 42})
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ---------------------------------------------------------------------------
# t42 — agent prompt includes editorial branch
# ---------------------------------------------------------------------------
def test_t42_placements_prompt_has_editorial_branch():
    """The placements sub-step guidance must mention the editorial branch
    (3 candidates, reviews API, no inline re-resolution)."""
    g = server._PLACEMENTS_SUBSTEP_GUIDANCE
    assert isinstance(g, str)
    low = g.lower()
    assert "editorial" in low
    assert "3" in g  # 3 candidates
    assert "povison-reviews" in low or "reviews" in low
    # Must instruct NOT to re-resolve inline links in editorial mode.
    assert "re-resolve" in low or "inline" in low
    # Editorial hard rules present.
    assert "exactly 3" in low
    assert "plain text" in low or "no link" in low
    assert "image" in low  # PDP link on the image
