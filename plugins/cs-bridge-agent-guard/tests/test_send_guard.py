"""Tests for cs-bridge-agent-guard send guard."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"cs_guard_test_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_blocks_quickcep_send_email_numeric_session():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="terminal",
        args={"command": "python3 quickcep_cli.py send-email 2544719278035312643 --subject Re --body hi"},
        task_id="povison-cs:LIVE:2544719278035312643",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "draft-save" in out["message"]


def test_blocks_execute_code_send_email_api():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="execute_code",
        args={"code": "requests.post('https://app.quickcep.com/im/message/operator/sendEmail', json={})"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is not None
    assert out["action"] == "block"


def test_allows_draft_save():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="terminal",
        args={"command": "python3 cs_bridge_tool.py draft-save --session-id 123 --content-file /tmp/x.html"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is None


def test_ignores_non_cs_sessions():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="terminal",
        args={"command": "python quickcep_cli.py send-email 123 --body hi"},
        session_id="kol-campaign:LIVE:99",
    )
    assert out is None


def test_cli_guard_blocks_povison_profile(monkeypatch):
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    monkeypatch.setenv("CS_OPS_PROFILE", "povison-cs")
    monkeypatch.delenv("CS_OPS_ALLOW_QUICKCEP_SEND", raising=False)
    assert sg.should_block_cli_send_email() is True


def test_cli_guard_manual_override(monkeypatch):
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    monkeypatch.setenv("CS_OPS_PROFILE", "povison-cs")
    monkeypatch.setenv("CS_OPS_ALLOW_QUICKCEP_SEND", "1")
    assert sg.should_block_cli_send_email() is False


def test_blocks_direct_quickcep_cli_messages():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="terminal",
        args={"command": "python3 quickcep_cli.py messages 2544719278035312643 --plain"},
        session_id="povison-cs:LIVE:2544719278035312643",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "cs_bridge_tool" in out["message"]


def test_allows_cs_bridge_tool_get_messages():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="terminal",
        args={"command": "python3 cs_bridge_tool.py get-messages --env LIVE --session-id 123"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is None


def test_blocks_direct_quickcep_cli_in_execute_code():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="execute_code",
        args={"code": "import subprocess; subprocess.run(['python3','quickcep_cli.py','messages','123'])"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "cs_bridge_tool" in out["message"]


def test_allows_cs_bridge_tool_in_execute_code():
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    out = sg.pre_tool_block(
        tool_name="execute_code",
        args={"code": "subprocess.run(['python3','cs_bridge_tool.py','get-messages','--session-id','123'])"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is None


def test_quickcep_cli_override(monkeypatch):
    sg = _load("send_guard", PLUGIN_ROOT / "send_guard.py")
    monkeypatch.setenv("CS_OPS_PROFILE", "povison-cs")
    monkeypatch.setenv("CS_OPS_ALLOW_QUICKCEP_CLI", "1")
    out = sg.pre_tool_block(
        tool_name="terminal",
        args={"command": "python3 quickcep_cli.py messages 123"},
        session_id="povison-cs:LIVE:12345",
    )
    assert out is None
