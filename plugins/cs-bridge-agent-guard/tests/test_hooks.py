"""Tests for cs-bridge-agent-guard pre_tool_call hook."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_bridge_agent_guard_test_pkg"


def _hooks():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    for sub in ("send_guard", "hooks"):
        full = f"{_PKG}.{sub}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(
            full,
            PLUGIN_ROOT / f"{sub}.py",
            submodule_search_locations=[str(PLUGIN_ROOT)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG
        sys.modules[full] = mod
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        setattr(sys.modules[_PKG], sub, mod)
    return sys.modules[f"{_PKG}.hooks"]


def test_blocks_quickcep_send_email():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {"command": "python quickcep_cli.py send-email <session> --body hi"},
        task_id="povison-cs:LIVE:12345",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "draft-save" in out["message"]


def test_blocks_quickcep_draft_save():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {"command": "python quickcep_cli.py draft-save <session> --body hi"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "cs_bridge_tool" in out["message"]


def test_allows_cs_bridge_tool_draft_save():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {"command": "python cs_bridge_tool.py draft-save --session-id 123 --content-file /tmp/x.html"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is None


def test_ignores_non_cs_sessions():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {"command": "python quickcep_cli.py send-email <session>"},
        session_id="kol-campaign:LIVE:99",
    )
    assert out is None
