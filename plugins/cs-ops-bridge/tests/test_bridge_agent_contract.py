"""Tests for gateway brief agent tool paths."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_contract_test"


def _load(name: str, path: Path):
    if _PKG not in sys.modules:
        sys.modules[_PKG] = types.ModuleType(_PKG)
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


profile_refs = _load("profile_refs", _PLUGIN_ROOT / "profile_refs.py")
contract = _load("bridge_agent_contract", _PLUGIN_ROOT / "bridge_agent_contract.py")


def test_agent_tool_paths_single_entry() -> None:
    paths = contract.agent_tool_paths()
    assert set(paths.keys()) == {"cs_bridge_tool", "bridge_env"}
    assert Path(paths["cs_bridge_tool"]).is_file()


def test_process_checklist_uses_cs_bridge_tool_only() -> None:
    cli = contract.cs_bridge_cli_path()
    text = contract.process_cli_checklist(env="LIVE", quickcep_session_id="sess-1")
    assert str(cli) in text
    assert "quickcep_cli" not in text
    assert "get-messages --env LIVE --session-id sess-1" in text
    assert "draft-save --env LIVE --session-id sess-1 --content-file" in text
    assert "agent_tool_paths" in text


def test_process_instructions_forbid_quickcep_cli() -> None:
    text = contract.process_instructions()
    assert "Do not call quickcep_cli directly" in text
    assert "cs_bridge_tool" in text
