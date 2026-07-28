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
    assert "Do **NOT** call quickcep_cli directly" in text
    assert "python3" in text and "quickcep_cli" not in text.split("bridge_cli_checklist", 1)[1]
    assert "get-messages --env LIVE --session-id sess-1" in text
    assert "draft-save --env LIVE --session-id sess-1 --content-file" in text
    assert "agent_tool_paths" in text
    assert "terminal:" in text


def test_process_instructions_forbid_quickcep_cli() -> None:
    text = contract.process_instructions()
    assert "Do not call quickcep_cli directly" in text
    assert "cs_bridge_tool" in text
    assert "execute_code" in text
    assert "terminal" in text
    assert "Do NOT use delegate_task" in text


def test_gateway_toolsets_exclude_delegation_and_code_execution() -> None:
    toolsets = _load("gateway_toolsets", _PLUGIN_ROOT / "gateway_toolsets.py")
    names = set(toolsets.POVISON_CS_API_SERVER_TOOLSETS)
    assert "terminal" in names
    assert "delegation" not in names
    assert "code_execution" not in names


def test_resume_checklist_forbids_execute_code() -> None:
    text = contract.resume_cli_checklist(env="LIVE", escalation_id=17)
    assert "execute_code" in text
    assert "terminal` tool IS in your available tool list" in text
    assert "delegate_task is PROHIBITED" in text
    assert "skill_view(name='povison-cs-escalation-resumer')" in text
    assert "terminal:" in text
    assert "quickcep_session_id" in text
    # Step 5 now uses the knowledge_retain MCP tool + knowledge bank (bank isolation)
    assert "knowledge_retain" in text
    assert "furniture-knowledge" in text
    assert "povison-cs-hermes-user" not in text
    assert "povison-cs-hermes-knowledge" not in text  # legacy name from plan; deployed bank is furniture-knowledge
    assert "192.168.10.123:8888" in text
    assert "items" in text  # curl payload wraps items (OpenAPI)


def test_process_checklist_step65_uses_knowledge_recall_mcp() -> None:
    text = contract.process_cli_checklist(env="LIVE", quickcep_session_id="sess-1")
    assert "knowledge_recall" in text
    assert "hindsight_recall(" not in text  # legacy tool call form replaced (tracker script name is allowed)
    assert "knowledge_retain" not in text.split("# bridge_cli_checklist", 1)[1].split("6.5.", 1)[0]  # not in process pre-6.5
    # policy questions with no SKU must not be skipped
    assert "never skip just because there is no SKU" in text
