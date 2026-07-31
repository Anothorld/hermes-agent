"""Step 1 — povison_reviews.py unit tests.

Mocks mysql.connector so tests run with no DB and no env. Asserts the SQL
contract (status_id=1 filter, rating filter, ORDER BY, LIMIT), the rating
percent→star conversion, and that is_configured() reflects env presence.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import povison_reviews


# ── is_configured ─────────────────────────────────────────────────────────


def test_t11_is_configured_false_without_env(monkeypatch):
    """No MAGENTO_DB_* env → is_configured() is False."""
    for k in ("MAGENTO_DB_HOST", "MAGENTO_DB_PASS", "MAGENTO_DB_USER", "MAGENTO_DB_NAME"):
        monkeypatch.delenv(k, raising=False)
    assert povison_reviews.is_configured() is False


def test_t11_is_configured_true_with_host_and_pass(monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "35.233.185.233")
    monkeypatch.setenv("MAGENTO_DB_PASS", "secret")
    assert povison_reviews.is_configured() is True


# ── fetch_reviews SQL contract ────────────────────────────────────────────


def _build_cursor(rows):
    """A mock context-managed dictionary cursor that returns ``rows``."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else {}
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    return cur


def _build_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_t11_fetch_reviews_returns_empty_when_not_configured(monkeypatch):
    for k in ("MAGENTO_DB_HOST", "MAGENTO_DB_PASS"):
        monkeypatch.delenv(k, raising=False)
    assert povison_reviews.fetch_reviews(spu=123, limit=5) == []


def test_t11_fetch_reviews_sql_has_status_filter_orderby_limit(monkeypatch):
    """The executed SQL must filter APPROVED (status_id=1), order by rating
    then helpfulness, and apply LIMIT — the security/ordering contract."""
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    cur = _build_cursor([])
    conn = _build_conn(cur)
    with patch("mysql.connector.connect", return_value=conn):
        povison_reviews.fetch_reviews(spu=42, limit=5)
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args.args[0], cur.execute.call_args.args[1]
    assert "status_id = 1" in sql
    assert "ORDER BY mpd.rating_sum DESC, helpful_count DESC" in sql
    assert "LIMIT %s" in sql
    # params: (spu, spu, limit)
    assert params == (42, 42, 5)


def test_t11_fetch_reviews_min_rating_adds_filter(monkeypatch):
    """min_rating (star units) → an `rating_sum >= min*20` clause appears."""
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    cur = _build_cursor([])
    conn = _build_conn(cur)
    with patch("mysql.connector.connect", return_value=conn):
        povison_reviews.fetch_reviews(spu=42, limit=3, min_rating=4)
    sql = cur.execute.call_args.args[0]
    assert "mpd.rating_sum >= 80" in sql  # 4 stars * 20


def test_t11_fetch_reviews_rating_conversion(monkeypatch):
    """rating_sum (0-100) → stars (1-5). 100→5, 80→4, 40→2, 0→None."""
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    rows = [
        {"review_id": 1, "nickname": "Megan", "title": "t", "detail": "d",
         "review_date": "2025-10-17", "rating_sum": 100, "helpful_count": 5, "source_type": "ORDER"},
        {"review_id": 2, "nickname": "Colin", "title": "t2", "detail": "d2",
         "review_date": "2025-01-23", "rating_sum": 80, "helpful_count": 2, "source_type": "ORDER"},
        {"review_id": 3, "nickname": "Low", "title": "t3", "detail": "d3",
         "review_date": "2025-03-03", "rating_sum": 40, "helpful_count": 0, "source_type": "ORDER"},
        {"review_id": 4, "nickname": "Zero", "title": "t4", "detail": "d4",
         "review_date": "2025-04-04", "rating_sum": 0, "helpful_count": 0, "source_type": "ORDER"},
    ]
    cur = _build_cursor(rows)
    conn = _build_conn(cur)
    with patch("mysql.connector.connect", return_value=conn):
        out = povison_reviews.fetch_reviews(spu=42, limit=10)
    assert out[0]["rating"] == 5
    assert out[1]["rating"] == 4
    assert out[2]["rating"] == 2
    assert out[3]["rating"] is None
    # Field shape:
    assert out[0]["nickname"] == "Megan"
    assert out[0]["date"] == "2025-10-17"
    assert out[0]["helpfulCount"] == 5
    assert out[0]["sourceType"] == "ORDER"


def test_t11_fetch_reviews_db_error_returns_empty(monkeypatch):
    """A connector error must not bubble up; we log and return []."""
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    with patch("mysql.connector.connect", side_effect=povison_reviews.mysql.connector.Error("boom")):
        assert povison_reviews.fetch_reviews(spu=42) == []


def test_t11_fetch_reviews_invalid_spu_returns_empty(monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    assert povison_reviews.fetch_reviews(spu="not-a-number") == []


def test_t11_fetch_reviews_limit_clamped(monkeypatch):
    """limit must be clamped to 1..50 even if caller asks for 9999."""
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    cur = _build_cursor([])
    conn = _build_conn(cur)
    with patch("mysql.connector.connect", return_value=conn):
        povison_reviews.fetch_reviews(spu=1, limit=9999)
    params = cur.execute.call_args.args[1]
    assert params[-1] == 50


# ── fetch_summary ─────────────────────────────────────────────────────────


def test_t11_fetch_summary_returns_aggregate(monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    cur = _build_cursor([{"reviews_count": 23, "rating_summary": 92}])
    conn = _build_conn(cur)
    with patch("mysql.connector.connect", return_value=conn):
        out = povison_reviews.fetch_summary(spu=42)
    assert out["reviewsCount"] == 23
    assert out["ratingSummary"] == 92
    assert out["rating"] == 4  # 92//20 = 4


def test_t11_fetch_summary_no_reviews(monkeypatch):
    monkeypatch.setenv("MAGENTO_DB_HOST", "h")
    monkeypatch.setenv("MAGENTO_DB_PASS", "p")
    cur = _build_cursor([{"reviews_count": 0, "rating_summary": 0}])
    conn = _build_conn(cur)
    with patch("mysql.connector.connect", return_value=conn):
        out = povison_reviews.fetch_summary(spu=42)
    assert out["reviewsCount"] == 0
    assert out["rating"] is None


# ── rating conversion edge cases ──────────────────────────────────────────


@pytest.mark.parametrize(
    "rating_sum,expected",
    [(100, 5), (99, 4), (80, 4), (60, 3), (40, 2), (20, 1), (1, 1), (0, None), (None, None), ("x", None)],
)
def test_t11_rating_to_stars(rating_sum, expected):
    assert povison_reviews._rating_to_stars(rating_sum) == expected
