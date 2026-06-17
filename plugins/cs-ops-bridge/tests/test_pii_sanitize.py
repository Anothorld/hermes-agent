"""Tests for PII sanitization."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pii_test"


def _load_pii():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.pii_sanitize"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "pii_sanitize.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_mask_email_in_string():
    pii = _load_pii()
    out = pii.mask_string("Contact alice@example.com for help")
    assert "alice@example.com" not in out
    assert "@example.com" in out


def test_sanitize_facts_namespace():
    pii = _load_pii()
    ns = {
        "customer": {
            "email": "bob@test.com",
            "phone": "+1 415-555-0100",
            "note": "plain text",
        }
    }
    clean, adj = pii.sanitize_namespaces(ns)
    assert "bob@test.com" not in str(clean)
    assert "555-0100" not in str(clean)
    assert adj
