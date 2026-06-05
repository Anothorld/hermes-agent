"""Tests for Veedcrawl monthly persist layer."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "kol_ops_bridge_veedcrawl_pkg"


def _load_modules(tmp_path: Path):
    if _PKG_NAME in sys.modules:
        pkg = sys.modules[_PKG_NAME]
    else:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(_PLUGIN_ROOT)]
        sys.modules[_PKG_NAME] = pkg

    loaded = {}
    for sub in ("schema", "campaign_nox_integration", "goals", "policies", "outreach_touch",
                "prior_touch_allowlist", "cal", "veedcrawl_cache", "veedcrawl_facts",
                "veedcrawl_persist"):
        key = f"{_PKG_NAME}.{sub}"
        if key in sys.modules:
            loaded[sub] = sys.modules[key]
            continue
        spec = importlib.util.spec_from_file_location(key, _PLUGIN_ROOT / f"{sub}.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
        loaded[sub] = mod
    return loaded


@pytest.fixture()
def veedcrawl_mods(tmp_path):
    mods = _load_modules(tmp_path)
    cache_db = tmp_path / "veedcrawl_cache.db"
    cal_db = tmp_path / "cal.db"
    mods["veedcrawl_cache"].set_db_path(cache_db)
    mods["cal"].set_db_path(cal_db)
    yield mods
    mods["veedcrawl_cache"].set_db_path(None)
    mods["cal"].set_db_path(None)


def test_cache_hit_zero_api_calls(veedcrawl_mods):
    persist = veedcrawl_mods["veedcrawl_persist"]
    calls = {"n": 0}

    def fetch_fn():
        calls["n"] += 1
        return {"stats": {"followers": 120000}, "videos": []}

    r1 = persist.fetch_with_persist(
        operation="get_instagram_profile",
        request={"username": "testcreator", "limit": 12},
        fetch_fn=fetch_fn,
        env="TEST",
    )
    assert r1["ok"] is True
    assert r1["cache_hit"] is False
    assert r1["api_calls"] == 1
    assert r1["persisted"] is True
    assert calls["n"] == 1

    r2 = persist.fetch_with_persist(
        operation="get_instagram_profile",
        request={"username": "testcreator", "limit": 12},
        fetch_fn=fetch_fn,
        env="TEST",
    )
    assert r2["cache_hit"] is True
    assert r2["api_calls"] == 0
    assert calls["n"] == 1


def test_search_cache_key_global(veedcrawl_mods):
    facts = veedcrawl_mods["veedcrawl_facts"]
    k1 = facts.build_cache_key("search_social_videos", {"q": "cozy home", "platform": "instagram", "limit": 10})
    k2 = facts.build_cache_key("search_social_videos", {"q": "cozy home", "platform": "instagram", "limit": 10})
    assert k1 == k2
    assert k1.startswith("search:")


def test_profile_facts_shape(veedcrawl_mods):
    facts = veedcrawl_mods["veedcrawl_facts"]
    out = facts.identity_facts_from_response(
        "get_instagram_profile",
        {
            "stats": {"followers": "416K"},
            "videos": [
                {"url": "https://www.instagram.com/reel/abc/", "stats": {"views": 50000, "likes": 1200}},
            ],
        },
        cache_month="2026-06",
        cache_key="profile:ig:foo:limit=12",
        handle="foo",
    )
    assert out["identity.veedcrawl_profile_followers"] == 416_000
    assert isinstance(out["identity.veedcrawl_recent_reels_stats"], list)
    assert out["identity.veedcrawl_recent_reels_stats"][0]["views"] == 50000


def test_storage_ref_inline_sqlite(veedcrawl_mods):
    cache = veedcrawl_mods["veedcrawl_cache"]
    month = cache.current_cache_month("UTC")
    cache.store(month, "meta:test", "get_video_metadata", {"viewCount": 1})
    ref = cache.storage_ref_for(month, "meta:test")
    assert ref.startswith("sqlite:")
    hit = cache.lookup(month, "meta:test", tz_name="UTC")
    assert hit is not None
    assert hit["storage_ref"] == ref


def test_prune_removes_old_fetch_log(veedcrawl_mods):
    cache = veedcrawl_mods["veedcrawl_cache"]
    with cache._connect() as conn:
        conn.execute(
            "INSERT INTO entries (cache_month, cache_key, operation, response_json, fetched_at) "
            "VALUES ('2020-01', 'old:key', 'search_social_videos', '{}', '2020-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO fetch_log (cache_month, cache_key, operation, cache_hit, env, identity_id, fetched_at) "
            "VALUES ('2020-01', 'old:key', 'search_social_videos', 0, 'TEST', NULL, '2020-01-01T00:00:00Z')"
        )
        conn.commit()
    cache.prune_old_months(3, tz_name="UTC")
    with cache._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM fetch_log WHERE cache_month = '2020-01'"
        ).fetchone()["c"]
    assert n == 0


def test_extract_facts_from_api_response(veedcrawl_mods):
    facts = veedcrawl_mods["veedcrawl_facts"]
    out = facts.identity_facts_from_response(
        "extract_from_video",
        {
            "job_id": "j1",
            "status": "completed",
            "api_response": {"jobId": "j1", "status": "completed", "resultJson": {"theme": "cozy"}},
        },
        cache_month="2026-06",
        cache_key="extract:x:abc",
    )
    assert "cozy" in str(out["identity.veedcrawl_extract_summary"])


def test_metadata_cache_key_is_hashed(veedcrawl_mods):
    facts = veedcrawl_mods["veedcrawl_facts"]
    long_url = "https://www.instagram.com/reel/" + ("a" * 300) + "/"
    key = facts.build_cache_key("get_video_metadata", {"url": long_url})
    assert key.startswith("metadata:")
    assert len(key) < 80
    assert long_url not in key


def test_search_authors_nested_shapes(veedcrawl_mods):
    facts = veedcrawl_mods["veedcrawl_facts"]
    out = facts.identity_facts_from_response(
        "search_social_videos",
        [
            {"author": {"username": "NestedUser"}},
            {"creator": "CreatorOne"},
            {"username": "TopLevel"},
        ],
        cache_month="2026-06",
        cache_key="search:abc",
    )
    assert out["identity.veedcrawl_search_authors"] == [
        "nesteduser",
        "creatorone",
        "toplevel",
    ]


def test_storage_ref_fact_alias(veedcrawl_mods):
    facts = veedcrawl_mods["veedcrawl_facts"]
    out = facts.identity_facts_from_response(
        "get_instagram_profile",
        {"stats": {"followers": 1000}, "videos": []},
        cache_month="2026-06",
        cache_key="profile:ig:foo:limit=12",
        blob_ref="sqlite:2026-06:profile:ig:foo:limit=12",
    )
    assert out["identity.veedcrawl_storage_ref"] == out["identity.veedcrawl_blob_ref"]


def test_extract_wait_false_skips_persist(veedcrawl_mods):
    persist = veedcrawl_mods["veedcrawl_persist"]
    cache = veedcrawl_mods["veedcrawl_cache"]
    month = cache.current_cache_month("UTC")

    r = persist.fetch_with_persist(
        operation="extract_from_video",
        request={"url": "https://www.instagram.com/reel/abc/", "prompt": "theme"},
        fetch_fn=lambda: {"job_id": "job-queued", "status": "queued"},
        env="TEST",
    )
    assert r["ok"] is True
    assert r["persisted"] is False
    assert r.get("pending_job") is True
    assert cache.lookup(month, r["cache_key"], tz_name="UTC") is None


def test_fetch_failure_envelope(veedcrawl_mods):
    persist = veedcrawl_mods["veedcrawl_persist"]

    def boom():
        raise RuntimeError("api down")

    r = persist.fetch_with_persist(
        operation="get_video_metadata",
        request={"url": "https://www.instagram.com/reel/abc/"},
        fetch_fn=boom,
        env="TEST",
    )
    assert r["ok"] is False
    assert r["persisted"] is False
    assert "api down" in r["error"]
