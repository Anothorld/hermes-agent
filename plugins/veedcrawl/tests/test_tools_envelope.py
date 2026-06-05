"""Plugin tool handlers return unified persist envelopes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from plugins.veedcrawl.tools import (
    _handle_extract,
    _handle_instagram_profile,
    _handle_job,
    _handle_metadata,
    _handle_search,
    _handle_transcript,
)


def _parse(result: str) -> dict:
    return json.loads(result)


_SAMPLE_ENVELOPE = {
    "ok": True,
    "operation": "get_video_metadata",
    "cache_month": "2026-06",
    "cache_key": "metadata:https://www.instagram.com/reel/abc/",
    "cache_hit": False,
    "api_calls": 1,
    "persisted": True,
    "blob_ref": None,
    "storage_ref": "sqlite:2026-06:metadata:https://www.instagram.com/reel/abc/",
    "identity_facts_written": False,
    "response": {"viewCount": 12000},
}


@patch("plugins.veedcrawl.tools.fetch_with_persist", return_value=_SAMPLE_ENVELOPE)
@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_metadata_envelope(mock_client_cls: MagicMock, _mock_persist: MagicMock) -> None:
    mock_client_cls.return_value.__enter__.return_value = MagicMock()
    out = _parse(_handle_metadata({"url": "https://www.instagram.com/reel/abc/"}))
    assert out["ok"] is True
    assert out["operation"] == "get_video_metadata"
    assert out["response"]["viewCount"] == 12000
    assert "storage_ref" in out


@patch("plugins.veedcrawl.tools.fetch_with_persist")
@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_search_envelope(mock_client_cls: MagicMock, mock_persist: MagicMock) -> None:
    mock_client_cls.return_value.__enter__.return_value = MagicMock()
    mock_persist.return_value = {
        **_SAMPLE_ENVELOPE,
        "operation": "search_social_videos",
        "response": [{"author": "foo", "url": "https://example.com"}],
    }
    out = _parse(_handle_search({"q": "cozy home", "platform": "instagram"}))
    assert out["operation"] == "search_social_videos"
    assert isinstance(out["response"], list)


@patch("plugins.veedcrawl.tools.fetch_with_persist")
@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_instagram_profile_envelope(mock_client_cls: MagicMock, mock_persist: MagicMock) -> None:
    mock_client_cls.return_value.__enter__.return_value = MagicMock()
    mock_persist.return_value = {
        **_SAMPLE_ENVELOPE,
        "operation": "get_instagram_profile",
        "response": {"stats": {"followers": 100000}, "videos": []},
    }
    out = _parse(_handle_instagram_profile({"username": "testcreator"}))
    assert out["operation"] == "get_instagram_profile"


@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_extract_job_id_returns_envelope(mock_client_cls: MagicMock) -> None:
    client = MagicMock()
    client.lookup_job.return_value = {
        "job_id": "job-1",
        "status": "completed",
        "result_json": {"score": 8},
        "api_response": {"jobId": "job-1", "status": "completed", "resultJson": {"score": 8}},
    }
    mock_client_cls.return_value.__enter__.return_value = client
    out = _parse(_handle_extract({"job_id": "job-1"}))
    assert out["ok"] is True
    assert out["job_lookup"] is True
    assert out["response"]["api_response"]["jobId"] == "job-1"


@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_transcript_job_id_returns_envelope(mock_client_cls: MagicMock) -> None:
    client = MagicMock()
    client.lookup_job.return_value = {"job_id": "t1", "status": "completed", "transcript": "hello"}
    mock_client_cls.return_value.__enter__.return_value = client
    out = _parse(_handle_transcript({"job_id": "t1"}))
    assert out["ok"] is True
    assert out["job_lookup"] is True
    assert out["operation"] == "get_video_transcript"


@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_job_lookup_returns_envelope(mock_client_cls: MagicMock) -> None:
    client = MagicMock()
    client.lookup_job.return_value = {"job_id": "j2", "status": "completed"}
    mock_client_cls.return_value.__enter__.return_value = client
    out = _parse(_handle_job({"endpoint": "extract", "job_id": "j2"}))
    assert out["ok"] is True
    assert out["job_lookup"] is True
    assert out["operation"] == "extract_from_video"


@patch("plugins.veedcrawl.tools.fetch_with_persist")
@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_extract_wait_false_pending_not_persisted(mock_client_cls: MagicMock, mock_persist: MagicMock) -> None:
    mock_client_cls.return_value.__enter__.return_value = MagicMock()
    mock_persist.return_value = {
        **_SAMPLE_ENVELOPE,
        "operation": "extract_from_video",
        "persisted": False,
        "pending_job": True,
        "response": {"job_id": "q1", "status": "queued"},
    }
    out = _parse(
        _handle_extract({
            "url": "https://www.instagram.com/reel/abc/",
            "prompt": "theme",
            "wait": False,
        })
    )
    assert out["persisted"] is False
    assert out.get("pending_job") is True


@patch("plugins.veedcrawl.tools.fetch_with_persist")
@patch("plugins.veedcrawl.tools.VeedcrawlClient")
def test_persist_failure_surfaces_error(mock_client_cls: MagicMock, mock_persist: MagicMock) -> None:
    mock_client_cls.return_value.__enter__.return_value = MagicMock()
    mock_persist.return_value = {
        "ok": False,
        "error": "upstream timeout",
        "persisted": False,
    }
    out = _parse(_handle_metadata({"url": "https://www.instagram.com/reel/abc/"}))
    assert "error" in out or out.get("message")
    assert out.get("persisted") is False or "upstream timeout" in str(out)
