"""Tests for reply dispatcher soft-gating controls."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_dispatcher():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kol_reply_dispatcher.py"
    spec = importlib.util.spec_from_file_location("kol_reply_dispatcher", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_delegated_budget_only_allows_autoflow():
    mod = _load_dispatcher()
    allow, controls = mod.resolve_autoflow_controls(
        content_risk="c2",
        thread_integrity="strict",
        identity_integrity="delegated",
        controls={"gate_budget": True, "gate_contract": False, "gate_payout": False},
    )
    assert allow is True
    assert controls["gate_budget"] is False


def test_delegated_budget_plus_payout_blocks():
    mod = _load_dispatcher()
    allow, controls = mod.resolve_autoflow_controls(
        content_risk="c2",
        thread_integrity="strict",
        identity_integrity="delegated",
        controls={"gate_budget": True, "gate_contract": False, "gate_payout": True},
    )
    assert allow is False
    assert controls["gate_budget"] is True


def test_unknown_budget_blocks():
    mod = _load_dispatcher()
    allow, _ = mod.resolve_autoflow_controls(
        content_risk="c2",
        thread_integrity="strict",
        identity_integrity="unknown",
        controls={"gate_budget": True, "gate_contract": False, "gate_payout": False},
    )
    assert allow is False
