"""Tests for cs_bridge_tool draft-save join-chat precondition."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
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


def _load_quickcep_join():
    """Load quickcep_join as a top-level module (matches cs_bridge_tool's import)."""
    if "quickcep_join" in sys.modules:
        return sys.modules["quickcep_join"]
    # _PLUGIN_ROOT is on sys.path (cs_bridge_tool adds it), so import directly.
    if str(_PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_ROOT))
    import quickcep_join as qj  # noqa: E402
    return qj


def test_draft_save_calls_join_chat_before_save(tmp_path, monkeypatch):
    tool = _load_cs_bridge_tool()
    qj = _load_quickcep_join()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    calls: list[list[str]] = []

    def fake_run(_cli, argv, timeout=120):
        calls.append(list(argv))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"action": "draft_save", "success": True, "result_code": 200}),
            stderr="",
        )

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)

    # Mock the shared join helper to return a legacy-compatible success result.
    join_result = {
        "ok": True,
        "source": "draft_save",
        "session_id": "sess-1",
        "result_code": 200,
        "attempts": 1,
        "error": None,
        "error_detail": None,
        "failed_step": None,
        "raw": {"action": "join_chat", "result_code": 200},
    }
    with patch.object(qj, "join_chat_session", return_value=join_result) as mock_join:
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

    # join_chat_session was called before the draft-save CLI
    mock_join.assert_called_once()
    join_call_kwargs = mock_join.call_args.kwargs
    assert join_call_kwargs["source"] == "draft_save"
    assert join_call_kwargs["raise_on_failure"] is False
    assert join_call_kwargs["max_attempts"] == tool.JOIN_CHAT_MAX_ATTEMPTS
    # draft-save CLI was called after join
    assert calls[0][0] == "draft-save"
    assert calls[0][1] == "sess-1"
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
