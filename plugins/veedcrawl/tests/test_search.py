"""Tests for async search job helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from plugins.veedcrawl._internal.errors import VeedcrawlJobFailedError
from plugins.veedcrawl._internal.search import (
    build_submit_body,
    normalize_search_items,
    run_search,
)


def test_build_submit_body_maps_q_to_query() -> None:
    body = build_submit_body(q="cozy home", platform="instagram", limit=12)
    assert body == {
        "query": "cozy home",
        "platforms": ["instagram"],
        "limit": 12,
    }


def test_build_submit_body_defaults_platforms() -> None:
    body = build_submit_body(q="ai tools", platform=None, limit=6)
    assert body["platforms"] == ["tiktok", "instagram", "youtube"]


def test_normalize_search_items_from_async_result() -> None:
    payload = {
        "status": "completed",
        "result": {
            "results": [
                {"url": "https://example.com/1", "author": {"username": "a"}},
            ],
        },
    }
    items = normalize_search_items(payload)
    assert len(items) == 1
    assert items[0]["url"].endswith("/1")


def test_normalize_search_items_legacy_array() -> None:
    payload = [{"url": "https://example.com/legacy"}]
    assert normalize_search_items(payload)[0]["url"].endswith("legacy")



def test_run_search_polls_until_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _request(method: str, path: str, **kwargs: object) -> tuple[dict, dict]:
        calls.append((method, path))
        if method == "POST" and path == "/v1/search":
            return (
                {"jobId": "job_search1", "status": "queued", "estimatedCredits": 12},
                {},
            )
        if method == "GET" and path == "/v1/search/job_search1":
            if sum(1 for m, p in calls if p == "/v1/search/job_search1") == 1:
                return ({"jobId": "job_search1", "status": "active"}, {})
            return (
                {
                    "jobId": "job_search1",
                    "status": "completed",
                    "result": {
                        "results": [
                            {
                                "platform": "instagram",
                                "url": "https://www.instagram.com/reel/abc/",
                                "author": {"username": "creator1"},
                            },
                        ],
                    },
                },
                {},
            )
        raise AssertionError(f"unexpected request {method} {path}")

    ensure = MagicMock()
    sleep = MagicMock()

    items = run_search(
        q="cozy home",
        platform="instagram",
        limit=3,
        force_refresh=True,
        timeout_s=30,
        request=_request,
        ensure_credits=ensure,
        sleep=sleep,
    )
    assert len(items) == 1
    assert items[0]["author"]["username"] == "creator1"
    assert ("POST", "/v1/search") in calls
    assert ensure.call_count >= 1


def test_run_search_raises_on_failed_job() -> None:
    def _request(method: str, path: str, **kwargs: object) -> tuple[dict, dict]:
        if method == "POST":
            return ({"jobId": "job_fail", "status": "queued", "estimatedCredits": 5}, {})
        return (
            {
                "jobId": "job_fail",
                "status": "failed",
                "error": {"message": "search quota exceeded"},
            },
            {},
        )

    with pytest.raises(VeedcrawlJobFailedError, match="quota exceeded"):
        run_search(
            q="test",
            platform="instagram",
            limit=3,
            force_refresh=True,
            request=_request,
            ensure_credits=MagicMock(),
            sleep=MagicMock(),
        )
