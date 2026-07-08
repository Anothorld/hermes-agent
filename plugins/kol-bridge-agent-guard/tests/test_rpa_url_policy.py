"""Tests for rpa_url_policy — URL block/allow logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_policy():
    path = _PLUGIN_ROOT / "internal" / "rpa_url_policy.py"
    spec = importlib.util.spec_from_file_location("rpa_url_policy_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rpa_url_policy_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_block_instagram():
    p = _load_policy()
    assert p.should_block_url("https://www.instagram.com/foo/") == True
    assert p.should_block_url("https://instagram.com/foo/reels/") == True


def test_block_google_search_only():
    p = _load_policy()
    assert p.should_block_url("https://www.google.com/search?q=foo") == True
    assert p.should_block_url("https://google.com/search?q=foo") == True
    # Non-search Google paths are NOT blocked
    assert p.should_block_url("https://docs.google.com/document/d/abc/edit") == False
    assert p.should_block_url("https://www.google.com/maps") == False
    assert p.should_block_url("https://www.google.com") == False


def test_block_ipinfo():
    p = _load_policy()
    assert p.should_block_url("https://ipinfo.io/json") == True


def test_allow_curated_lists():
    p = _load_policy()
    assert p.should_block_url("https://www.feedspot.com/furniture-blog/") == False
    assert p.should_block_url("https://influencerhero.com/top-10") == False


def test_allow_tiktok_reddit():
    p = _load_policy()
    assert p.should_block_url("https://www.tiktok.com/@creator") == False
    assert p.should_block_url("https://www.reddit.com/r/InteriorDesign/") == False


def test_allow_linktree_beacons():
    p = _load_policy()
    assert p.should_block_url("https://linktr.ee/creator") == False
    assert p.should_block_url("https://beacons.ai/creator") == False
    assert p.should_block_url("https://bio.link/creator") == False


def test_empty_url_not_blocked():
    p = _load_policy()
    assert p.should_block_url("") == False
    assert p.should_block_url(None) == False


def test_explicitly_allowed():
    p = _load_policy()
    assert p.is_explicitly_allowed("https://www.feedspot.com/foo") == True
    assert p.is_explicitly_allowed("https://www.instagram.com/foo") == False
