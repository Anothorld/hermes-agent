"""Tests for cs_bridge_tool draft-save join-chat precondition."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN_ROOT / "scripts"


def _load_cs_bridge_tool():
    name = "cs_bridge_tool_draft_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / "cs_bridge_tool.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_draft_save_calls_join_chat_before_save(tmp_path, monkeypatch):
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    calls: list[list[str]] = []

    def fake_run(_cli, argv, timeout=120):
        calls.append(list(argv))
        if argv[0] == "join-chat":
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"action": "join_chat", "result_code": 200}),
                stderr="",
            )
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"action": "draft_save", "success": True, "result_code": 200}),
            stderr="",
        )

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)

    args = MagicMock(
        session_id="sess-1",
        content="Hello",
        content_file=None,
        subject="Re: test",
        receiver="a@b.com",
    )
    with patch.object(tool, "print_json") as mock_print:
        tool._cmd_draft_save(args)

    assert calls[0] == ["join-chat", "sess-1"]
    assert calls[1][0] == "draft-save"
    assert calls[1][1] == "sess-1"
    mock_print.assert_called_once()
    out = mock_print.call_args[0][0]
    assert out["join_chat"]["result_code"] == 200
    assert out["success"] is True


def test_join_chat_retries_on_timeout_then_succeeds(tmp_path, monkeypatch):
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool.time, "sleep", lambda _s: None)

    attempts = {"n": 0}

    def fake_run(_cli, argv, timeout=120):
        attempts["n"] += 1
        if attempts["n"] < 2:
            return MagicMock(
                returncode=1,
                stdout=json.dumps(
                    {
                        "error": "<urlopen error timed out>",
                        "failed_step": "joinChat",
                        "command": "join-chat",
                    }
                ),
                stderr="",
            )
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"action": "join_chat", "result_code": 200}),
            stderr="",
        )

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)
    result = tool._join_chat_before_draft(cli, "sess-retry")
    assert result["result_code"] == 200
    assert result["join_chat_attempts"] == 2
    assert attempts["n"] == 2


def test_join_chat_failure_includes_failed_step(tmp_path, monkeypatch):
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool.time, "sleep", lambda _s: None)

    def fake_run(_cli, argv, timeout=120):
        return MagicMock(
            returncode=1,
            stdout=json.dumps(
                {
                    "error": "<urlopen error timed out>",
                    "failed_step": "getUserInfo",
                    "command": "join-chat",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)
    with patch.object(tool, "print_json") as mock_print, patch.object(
        tool.sys, "exit", side_effect=SystemExit(1)
    ):
        with pytest.raises(SystemExit):
            tool._join_chat_before_draft(cli, "sess-fail")

    err = mock_print.call_args[0][0]
    assert err["failed_step"] == "getUserInfo"
    assert "getUserInfo timed out" in err["error_detail"]
    assert err["attempt"] == tool.JOIN_CHAT_MAX_ATTEMPTS
