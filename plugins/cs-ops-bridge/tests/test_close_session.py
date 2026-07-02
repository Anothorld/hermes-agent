"""Tests for Console close-session (QuickCEP leave-chat + reviewed handoff)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_close_test"


def _load_pkg_module(sub: str):
    if _PKG not in sys.modules:
        import types

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


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal_close.db"))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


@pytest.fixture()
def close_module(cal):
    cal.enqueue_session(
        quickcep_session_id="qc-close",
        customer_email="close@example.com",
        message_id="m-close",
        email_subject="Close me",
    )
    return _load_pkg_module("close_session")


def _stub_cli(tmp_path, monkeypatch, close_module):
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(close_module, "_quickcep_cli_path", lambda: cli)
    return cli


def test_close_session_success_and_reviewed(close_module, monkeypatch, tmp_path, cal):
    cli = _stub_cli(tmp_path, monkeypatch, close_module)
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "chat_end": True}), "stderr": ""})()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)

    with patch.object(
        close_module,
        "apply_handoff",
        return_value={"ok": True, "phase": "reviewed"},
    ) as handoff:
        result = close_module.close_session(
            quickcep_session_id="qc-close",
            operator_id="op1",
            operator_name="Arnold",
        )

    assert result["ok"] is True
    assert captured["argv"][1:4] == [str(cli), "leave-chat", "qc-close"]
    handoff.assert_called_once()
    assert handoff.call_args.kwargs["phase"] == "reviewed"
    sess = cal.get_session(quickcep_session_id="qc-close")
    assert sess["status"] == "pending"  # handoff mocked — status unchanged


def test_close_session_quickcep_failure(close_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, close_module)

    def fake_run(argv, **kwargs):
        return type(
            "P",
            (),
            {
                "returncode": 2,
                "stdout": json.dumps({"ok": False, "error": "chat_end_not_confirmed"}),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(close_module.subprocess, "run", fake_run)
    result = close_module.close_session(quickcep_session_id="qc-close")
    assert result["ok"] is False
    assert result["error"] == "quickcep_close_failed"
