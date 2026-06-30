"""Tests for operator outbound detection."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_outbound_detect_test"


def _load():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.operator_outbound_detect"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "operator_outbound_detect.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_picks_operator_when_latest():
    mod = _load()
    picked = mod.pick_latest_operator_outbound_email(
        [
            {"ownerType": "operator", "contentType": "html", "id": "op-1"},
        ]
    )
    assert picked == {"id": "op-1", "createTime": ""}


def test_rejects_visitor_after_operator():
    mod = _load()
    picked = mod.pick_latest_operator_outbound_email(
        [
            {"ownerType": "visitor", "contentType": "html", "id": "v-2"},
            {"ownerType": "operator", "contentType": "html", "id": "op-1"},
        ]
    )
    assert picked is None


def test_skips_internal_note_before_operator():
    mod = _load()
    picked = mod.pick_latest_operator_outbound_email(
        [
            {"ownerType": "operatorNote", "contentType": "internalNote", "id": "n-1"},
            {"ownerType": "operator", "contentType": "html", "id": "op-1", "createTime": "t1"},
        ]
    )
    assert picked == {"id": "op-1", "createTime": "t1"}
