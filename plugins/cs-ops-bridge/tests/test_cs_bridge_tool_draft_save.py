"""Tests for cs_bridge_tool draft-save join-chat precondition."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
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

    # Explicitly exercise the legacy QuickCEP path (default now writes to CAL).
    args = MagicMock(
        session_id="sess-1",
        content="Hello",
        content_file=None,
        subject="Re: test",
        receiver="a@b.com",
        env="LIVE",
        legacy_quickcep_draft=True,
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


def test_draft_save_default_writes_to_cal_via_http(tmp_path, monkeypatch):
    """PR1.3: default draft-save writes to CAL via PUT /draft (no joinChat, no QC)."""
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    captured: dict[str, Any] = {}

    class FakeClient:
        def request(self, method, path, *, body=None, query=None):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return {"action": "draft_save", "success": True, "stored": "cal",
                    "session_id": "sess-cal", "source": "agent", "attachments": 0}

    monkeypatch.setattr(tool, "client_from_args", lambda _args: FakeClient())

    args = MagicMock(
        session_id="sess-cal",
        content="<p>Hello</p>",
        content_file=None,
        subject="Re: order",
        receiver=None,
        attachments=None,
        env="LIVE",
        legacy_quickcep_draft=False,
    )
    # Ensure the CAL branch is taken (MagicMock auto-attrs are truthy, so pin False).
    args.legacy_quickcep_draft = False
    with patch.object(tool, "print_json") as mock_print:
        tool._cmd_draft_save(args)

    assert captured["method"] == "PUT"
    assert captured["path"] == "/sessions/sess-cal/draft"
    assert captured["body"]["draft_html"] == "<p>Hello</p>"
    assert captured["body"]["source"] == "agent"
    assert captured["body"]["env"] == "LIVE"
    out = mock_print.call_args[0][0]
    assert out["stored"] == "cal"
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


def test_draft_save_rejects_shared_tmp_path(tmp_path, monkeypatch):
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    shared = Path("/tmp/draft.html")
    shared.write_text("unsafe shared draft", encoding="utf-8")

    args = MagicMock(
        session_id="sess-unsafe",
        content=None,
        content_file=str(shared),
        subject="Re: test",
        receiver="a@b.com",
        attachments=None,
    )
    with patch.object(tool, "print_json") as mock_print, patch.object(
        tool.sys, "exit", side_effect=SystemExit(2)
    ):
        with pytest.raises(SystemExit):
            tool._cmd_draft_save(args)

    out = mock_print.call_args[0][0]
    assert out["error"] == "unsafe shared content-file path /tmp/draft.html"
