"""Tests for QuickCEP intention tag gate."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_intent_gate_test"


def _load_module():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.intent_gate"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "intent_gate.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ig(monkeypatch):
    monkeypatch.delenv("CS_OPS_INTENT_FILTER", raising=False)
    monkeypatch.delenv("CS_OPS_ALLOWED_INTENTION_TAGS", raising=False)
    mod = _load_module()
    mod._load_config.cache_clear()
    return mod


def test_allowed_product_intent(ig):
    result = ig.check_intent_gate("sess-1", ["产品咨询"], fetch_if_missing=False)
    assert result.allowed is True
    assert result.reason == "allowed"


def test_allowed_logistics_intent(ig):
    result = ig.check_intent_gate("sess-2", ["物流咨询"], fetch_if_missing=False)
    assert result.allowed is True


def test_rejects_other_intent(ig):
    result = ig.check_intent_gate("sess-3", ["退货退款咨询"], fetch_if_missing=False)
    assert result.allowed is False
    assert "intention_not_allowed" in result.reason


def test_rejects_missing_intent_without_fetch(ig):
    result = ig.check_intent_gate("sess-4", None, fetch_if_missing=False)
    assert result.allowed is False
    assert result.reason == "no_intention_tags"


def test_filter_disabled_via_env(ig, monkeypatch):
    monkeypatch.setenv("CS_OPS_INTENT_FILTER", "false")
    ig._load_config.cache_clear()
    result = ig.check_intent_gate("sess-5", ["支付咨询"], fetch_if_missing=False)
    assert result.allowed is True
    assert result.reason == "filter_disabled"


def test_custom_allowed_tags_env(ig, monkeypatch):
    monkeypatch.setenv("CS_OPS_ALLOWED_INTENTION_TAGS", "支付咨询")
    result = ig.check_intent_gate("sess-6", ["支付咨询"], fetch_if_missing=False)
    assert result.allowed is True


def test_fetch_when_tags_missing(ig):
    with patch.object(ig, "fetch_session_intention_tags", return_value=("物流咨询",)):
        result = ig.check_intent_gate("sess-7", None, fetch_if_missing=True)
    assert result.allowed is True
    assert result.tags == ("物流咨询",)
