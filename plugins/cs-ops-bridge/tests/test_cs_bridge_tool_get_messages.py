"""Tests for cs_bridge_tool get-messages wrapper."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN_ROOT / "scripts"


def _load_cs_bridge_tool():
    name = "cs_bridge_tool_messages_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / "cs_bridge_tool.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_get_messages_wraps_quickcep_cli(tmp_path, monkeypatch):
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    calls: list[list[str]] = []

    def fake_run(_cli, argv):
        calls.append(list(argv))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"session_id": "sess-1", "messages": []}),
            stderr="",
        )

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)

    args = MagicMock(session_id="sess-1", page=0, page_size=20, plain=True, chronological=True)
    with patch.object(tool, "print_json") as mock_print:
        tool._cmd_get_messages(args)

    assert calls == [["messages", "sess-1", "--plain", "--chronological"]]
    mock_print.assert_called_once()
    out = mock_print.call_args[0][0]
    assert out["session_id"] == "sess-1"
