"""Tests for inbound reply autoflow gating."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def gating_mod():
    plugin_root = Path(__file__).resolve().parents[1]
    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.inbound_reply.gating",
        plugin_root / "inbound_reply" / "gating.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.inbound_reply.gating"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_delegated_budget_only_allows_autoflow(gating_mod):
    allow, controls = gating_mod.resolve_autoflow_controls(
        content_risk="c2",
        thread_integrity="strict",
        identity_integrity="delegated",
        controls={"gate_budget": True, "gate_contract": False, "gate_payout": False},
    )
    assert allow is True
    assert controls["gate_budget"] is False


def test_delegated_budget_plus_payout_blocks(gating_mod):
    allow, controls = gating_mod.resolve_autoflow_controls(
        content_risk="c2",
        thread_integrity="strict",
        identity_integrity="delegated",
        controls={"gate_budget": True, "gate_contract": False, "gate_payout": True},
    )
    assert allow is False
    assert controls["gate_budget"] is True


def test_unknown_budget_blocks(gating_mod):
    allow, _ = gating_mod.resolve_autoflow_controls(
        content_risk="c2",
        thread_integrity="strict",
        identity_integrity="unknown",
        controls={"gate_budget": True, "gate_contract": False, "gate_payout": False},
    )
    assert allow is False
