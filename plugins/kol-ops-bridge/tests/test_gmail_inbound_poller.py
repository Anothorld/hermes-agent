"""Tests for bridge-integrated inbound Gmail poller state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def inbound_poller_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib.util
    import sys
    import types

    plugin_root = Path(__file__).resolve().parents[1]
    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.gmail_inbound_poller",
        plugin_root / "gmail_inbound_poller.py",
    )
    assert spec and spec.loader
    monkeypatch.setenv("KOL_OPS_BRIDGE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KOL_OPS_GMAIL_INBOUND_AUTO_START", "0")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.gmail_inbound_poller"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_configure_start_stop_roundtrip(inbound_poller_mod):
    mod = inbound_poller_mod
    assert mod.get_status()["running"] is False

    started = mod.configure(
        enabled=True,
        env="LIVE",
        interval=90,
        lookback_days=5,
        max_results=25,
    )
    assert started["running"] is True
    assert started["env"] == "LIVE"
    assert started["interval"] == 90
    assert started["managed_by"] == "bridge"

    stopped = mod.configure(enabled=False)
    assert stopped["running"] is False
    assert stopped["stopped_at"]


def test_load_state_merges_file(inbound_poller_mod, tmp_path: Path):
    mod = inbound_poller_mod
    path = tmp_path / "inbound_poller.json"
    path.write_text(
        json.dumps({"enabled": True, "env": "TEST", "interval": 120}),
        encoding="utf-8",
    )
    status = mod.get_status()
    assert status["running"] is True
    assert status["interval"] == 120
