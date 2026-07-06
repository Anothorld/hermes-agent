"""Test cs-bridge-agent-guard register() startup self-check for missing env vars."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_bridge_agent_guard_selfcheck_test"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if key == _PKG or key.startswith(f"{_PKG}."):
            del sys.modules[key]


def _load_init():
    _reset_modules()
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.init_mod"
    spec = importlib.util.spec_from_file_location(
        full,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


class _FakeCtx:
    def __init__(self):
        self.hooks: dict[str, object] = {}

    def register_hook(self, name: str, fn) -> None:
        self.hooks[name] = fn


def test_selfcheck_warns_when_bridge_env_missing(caplog):
    init_mod = _load_init()
    ctx = _FakeCtx()
    with patch.dict("os.environ", {}, clear=False):
        # Ensure the two vars are absent.
        import os
        for var in ("CS_OPS_BRIDGE_BASE", "HERMES_CS_OPS_BRIDGE_KEY", "CS_OPS_BRIDGE_KEY"):
            os.environ.pop(var, None)
        with caplog.at_level(logging.WARNING, logger="cs_bridge_agent_guard_selfcheck_test.init_mod"):
            init_mod.register(ctx)
    assert "pre_tool_call" in ctx.hooks
    assert "on_session_end" in ctx.hooks
    assert any("degraded to 4h timeout" in rec.message for rec in caplog.records)


def test_selfcheck_silent_when_bridge_env_present(caplog):
    init_mod = _load_init()
    ctx = _FakeCtx()
    with patch.dict("os.environ", {
        "CS_OPS_BRIDGE_BASE": "http://127.0.0.1:8081",
        "HERMES_CS_OPS_BRIDGE_KEY": "secret",
    }, clear=False):
        with caplog.at_level(logging.WARNING, logger="cs_bridge_agent_guard_selfcheck_test.init_mod"):
            init_mod.register(ctx)
    assert not any("degraded to 4h timeout" in rec.message for rec in caplog.records)
