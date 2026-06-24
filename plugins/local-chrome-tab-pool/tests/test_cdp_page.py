"""Tests for direct page-level CDP navigation helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_cdp_page():
    path = Path(__file__).resolve().parents[1] / "internal" / "cdp_page.py"
    spec = importlib.util.spec_from_file_location("cdp_page_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_navigation_landed_on_tab_exact_match():
    cdp_page = _load_cdp_page()
    assert cdp_page.navigation_landed_on_tab(
        "https://www.instagram.com/angelarosehome/",
        "https://www.instagram.com/angelarosehome/",
    )


def test_navigation_landed_on_tab_rejects_cross_talk():
    cdp_page = _load_cdp_page()
    assert not cdp_page.navigation_landed_on_tab(
        "https://www.instagram.com/explore/tags/homecinema/",
        "https://www.instagram.com/angelarosehome/",
    )


def test_navigation_landed_on_tab_allows_instagram_tag_redirect():
    cdp_page = _load_cdp_page()
    assert cdp_page.navigation_landed_on_tab(
        "https://www.instagram.com/explore/tags/cozyliving/",
        "https://www.instagram.com/explore/search/keyword/?q=%23cozyliving",
    )


def test_navigation_landed_on_tab_rejects_blank():
    cdp_page = _load_cdp_page()
    assert not cdp_page.navigation_landed_on_tab(
        "https://www.instagram.com/angelarosehome/",
        "about:blank",
    )
