"""Tests for kol-bridge-agent-guard pre_tool_call hook."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _hooks():
    path = PLUGIN_ROOT / "hooks.py"
    spec = importlib.util.spec_from_file_location("kol_bridge_agent_guard_hooks_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_blocks_execute_code_curl():
    h = _hooks()
    out = h.pre_tool_call(
        "execute_code",
        {"code": 'BRIDGE_KEY = "x"\ncurl http://127.0.0.1:8080/api/plugins/kol-ops-bridge/health'},
        session_id="kol-campaign:LIVE:SEB",
    )
    assert out is not None
    assert out["action"] == "block"
    payload = json.loads(out["message"])
    assert payload["error"] == "bridge_agent_contract_violation"


def test_allows_terminal_kol_bridge_tool():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "get-escalation --escalation-id 108 --env LIVE"
            ),
        },
        session_id="kol-campaign:LIVE:SEB",
    )
    assert out is None


def test_blocks_read_plugin_api_on_kol_session():
    h = _hooks()
    out = h.pre_tool_call(
        "read_file",
        {"path": "plugins/kol-ops-bridge/plugin_api.py"},
        session_id="kol-reply:LIVE:667:msg",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_read_env_file():
    h = _hooks()
    out = h.pre_tool_call(
        "read_file",
        {"path": "/Users/me/.hermes/profiles/kol-orchestrator/.env"},
        session_id="kol-campaign:LIVE:C1",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_write_facts_reply_draft_in_execute_code():
    h = _hooks()
    out = h.pre_tool_call(
        "execute_code",
        {
            "code": (
                'write-facts-multi --json {"namespaces":'
                '{"approval": {"approval.reply_draft": {}}}}'
            ),
        },
        session_id="kol-campaign:LIVE:C1",
    )
    assert out is not None
