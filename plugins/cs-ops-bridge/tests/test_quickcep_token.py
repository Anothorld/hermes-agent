"""Tests for QuickCEP SIO token rebind and re-login observability."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_qt_test"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if key == _PKG or key.startswith(f"{_PKG}."):
            del sys.modules[key]


def _load(sub: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


def test_rebind_sio_get_valid_token_uses_patched_login():
    _reset_modules()
    qw = _load("quickcep_watcher")

    sio_mod = types.ModuleType("quickcep_sio_email_monitor")
    sio_mod.get_valid_token = lambda: "stale-bound"

    login_mod = types.ModuleType("quickcep_login")
    login_mod.get_valid_token = lambda: "patched-live"
    sys.modules["quickcep_login"] = login_mod

    qw._rebind_sio_get_valid_token(sio_mod)
    assert sio_mod.get_valid_token() == "patched-live"


def test_fetch_last_operator_message_logs_warning_on_cli_failure(monkeypatch, caplog):
    _reset_modules()
    osr = _load("operator_send_reconcile")

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = '{"error": "No valid token"}'

    with patch.object(osr.subprocess, "run", return_value=_Proc()):
        with patch.object(osr, "_quickcep_subprocess_env", return_value={}):
            with caplog.at_level(logging.WARNING):
                assert osr._fetch_last_operator_message("2547535874648612865") is None

    assert any("messages failed" in r.message for r in caplog.records)
