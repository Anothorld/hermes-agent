"""Tests for cs-bridge-agent-guard pre_tool_call hook."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _hooks():
    path = PLUGIN_ROOT / "hooks.py"
    spec = importlib.util.spec_from_file_location("cs_bridge_agent_guard_hooks_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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


def test_allows_draft_save():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {"command": "python quickcep_cli.py draft-save <session> --body hi"},
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
