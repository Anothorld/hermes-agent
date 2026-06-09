"""Tests for unified Gmail worker scheduling helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def worker_mod(monkeypatch: pytest.MonkeyPatch):
    plugin_root = Path(__file__).resolve().parents[1]
    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg

    for sub in (
        "gmail_inbound_dispatch",
        "gmail_inbound_poller",
        "gmail_poller",
        "gmail_worker",
    ):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{sub}",
            plugin_root / f"{sub}.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{sub}"] = mod
        spec.loader.exec_module(mod)

    monkeypatch.setenv("KOL_OPS_GMAIL_WORKER_PARALLEL", "0")
    return sys.modules[f"{pkg_name}.gmail_worker"]


def test_inbound_due_first_run(worker_mod):
    mod = worker_mod
    assert mod._inbound_due(last_mono=0.0, interval_sec=60, now_mono=100.0) is True


def test_inbound_not_due_within_interval(worker_mod):
    mod = worker_mod
    assert mod._inbound_due(last_mono=100.0, interval_sec=60, now_mono=140.0) is False
    assert mod._inbound_due(last_mono=100.0, interval_sec=60, now_mono=161.0) is True


def test_sent_due_symmetric(worker_mod):
    mod = worker_mod
    assert mod._sent_due(last_mono=0.0, interval_sec=300, now_mono=1.0) is True
    assert mod._sent_due(last_mono=50.0, interval_sec=300, now_mono=200.0) is False
