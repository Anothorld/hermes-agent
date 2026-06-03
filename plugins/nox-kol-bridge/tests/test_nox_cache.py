"""Tests for monthly nox_cache."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from schemas import RESPONSE_BLOB_THRESHOLD_BYTES  # noqa: E402

from internal import commands, nox_cache  # noqa: E402
from internal.normalize import cache_key_diligence  # noqa: E402


def test_diligence_cache_hit_zero_api_calls(nox_home):
    r1 = commands.cmd_diligence_pack(
        env="TEST",
        gate="shortlist_confirm",
        monthly_budget=100,
        tz_name="UTC",
        lang="en",
        nox_creator_id="nox_test_creator_001",
        platform=None,
        url=None,
        channel_id=None,
        dimensions=["profile", "audience", "content"],
        include_cooperation=False,
    )
    assert r1["cache_hit"] is False
    assert r1["api_calls"] == 3

    r2 = commands.cmd_diligence_pack(
        env="TEST",
        gate="shortlist_confirm",
        monthly_budget=100,
        tz_name="UTC",
        lang="en",
        nox_creator_id="nox_test_creator_001",
        platform=None,
        url=None,
        channel_id=None,
        dimensions=["profile", "audience", "content"],
        include_cooperation=False,
    )
    assert r2["cache_hit"] is True
    assert r2["api_calls"] == 0


def test_alias_url_then_id_same_month(nox_home):
    commands.cmd_diligence_pack(
        env="TEST",
        gate="shortlist_confirm",
        monthly_budget=100,
        tz_name="UTC",
        lang="en",
        nox_creator_id=None,
        platform="youtube",
        url="https://www.youtube.com/@TestCreator",
        channel_id=None,
        dimensions=["profile"],
        include_cooperation=False,
    )
    cid = nox_cache.resolve_alias(
        "youtube", "https://www.youtube.com/@TestCreator"
    )
    assert cid == "nox_test_creator_001"


def test_large_response_stored_as_blob(nox_home):
    month = nox_cache.current_cache_month("UTC")
    big = {"payload": "x" * (RESPONSE_BLOB_THRESHOLD_BYTES + 500)}
    nox_cache.store(month, "blob_test_key", "creator_search", big)
    hit = nox_cache.lookup(month, "blob_test_key")
    assert hit is not None
    assert hit["response"] == big


def test_quota_exceeded(nox_home):
    with pytest.raises(Exception) as exc:
        commands.cmd_contacts(
            env="TEST",
            gate="pre_outreach_confirm",
            monthly_budget=0,
            tz_name="UTC",
            lang="en",
            nox_creator_id="nox_test_creator_001",
            platform=None,
            url=None,
        )
    assert "budget" in str(exc.value).lower() or "QUOTA" in str(exc.value)
