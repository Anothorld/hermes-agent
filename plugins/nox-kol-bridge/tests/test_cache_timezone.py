"""Campaign config timezone override."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.campaign_gate import resolve_cache_timezone  # noqa: E402


def test_resolve_cache_timezone_prefers_campaign():
    cfg = {"nox_cache_timezone": "America/New_York"}
    assert resolve_cache_timezone(cfg, "UTC") == "America/New_York"


def test_resolve_cache_timezone_falls_back():
    assert resolve_cache_timezone({}, "Asia/Shanghai") == "Asia/Shanghai"
