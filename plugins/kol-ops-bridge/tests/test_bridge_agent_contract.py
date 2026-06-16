"""Tests for shared bridge_agent_contract lint rules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _contract():
    path = PLUGIN_ROOT / "bridge_agent_contract.py"
    spec = importlib.util.spec_from_file_location("bridge_agent_contract_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_lint_subprocess_bridge_cli():
    c = _contract()
    bad = (
        "import subprocess\n"
        "subprocess.run(['python', 'plugins/kol-ops-bridge/scripts/kol_bridge_tool.py', "
        "'health'])\n"
    )
    hits = c.lint_agent_bridge_snippet(bad)
    assert any(h["code"] == "bridge_cli_via_execute_code" for h in hits)


def test_lint_patch_escalation():
    c = _contract()
    bad = 'curl -X PATCH "http://127.0.0.1:8080/api/plugins/kol-ops-bridge/escalations/108"'
    hits = c.lint_agent_bridge_snippet(bad)
    assert any(h["code"] == "patch_escalation_http" for h in hits)


def test_lint_batch_ingest_tmp():
    c = _contract()
    bad = "paths = ['/tmp/ingest_foo.json', '/tmp/ingest_bar.json']\nfor p in paths: ..."
    hits = c.lint_agent_bridge_snippet(bad)
    assert any(h["code"] == "batch_ingest_files" for h in hits)


def test_lint_file_tool_blocks_plugin_api():
    c = _contract()
    hits = c.lint_file_tool_path("plugins/kol-ops-bridge/plugin_api.py")
    assert hits and hits[0]["code"] == "read_bridge_source_file"


def test_dispatch_context_cli_line_uses_agent_view():
    c = _contract()
    line = c.dispatch_context_cli_line(
        identity_id=42, campaign_id="CID-1", env="LIVE",
    )
    assert "--view agent" in line
    assert "get-dispatch-context" in line


def test_resume_checklist_includes_get_escalation():
    c = _contract()
    text = c.resume_cli_checklist(
        escalation_id=108,
        identity_id=667,
        campaign_id="SEB8008-20260525",
        env="LIVE",
        require_draft=False,
    )
    assert "get-escalation --escalation-id 108" in text
    assert "--view agent" in text
    assert "get-email-conversation" not in text


def test_approval_checklist_uses_persist_initial_outreach():
    c = _contract()
    text = c.approval_cli_checklist(
        campaign_id="POVISON-TS-8319-20260603",
        env="LIVE",
        identity_ids=[689, 695],
    )
    assert "persist-initial-outreach-draft" in text
    assert "draft:outreach_POVISON-TS-8319-20260603_689" in text
    assert "write-facts-multi" not in text or "Never write" in text


def test_approval_checklist_absolute_paths_when_repo_root_set():
    c = _contract()
    repo = str(PLUGIN_ROOT)
    text = c.approval_cli_checklist(
        campaign_id="SEB8008-20260525",
        env="LIVE",
        identity_ids=[820],
        repo_root=repo,
    )
    cli = c.cli_invocation_abs(repo)
    assert cli in text
    assert "kol_bridge_tool.py" in text
    assert "python3 -u" in cli
    assert "python3 plugins/kol-ops-bridge" not in text
    assert "kol-bridge-cli" not in text.split("get-campaign")[0]
    sample_cmd = f"{cli} get-campaign --campaign-id SEB8008-20260525 --env LIVE"
    assert c.lint_terminal_command(sample_cmd) == []


def test_gateway_contract_block_absolute_when_repo_root_set():
    c = _contract()
    repo = str(PLUGIN_ROOT)
    text = c.gateway_contract_block(repo_root=repo)
    cli = c.cli_invocation_abs(repo)
    assert cli in text
    assert "python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py" not in text


def test_blocks_python3_on_kol_bridge_cli_wrapper():
    c = _contract()
    bad = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli "
        "get-campaign --campaign-id X --env LIVE"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "python3_on_kol_bridge_cli_wrapper" for h in hits)


def test_redraft_cli_checklist_writes_only():
    c = _contract()
    repo = str(PLUGIN_ROOT)
    text = c.redraft_cli_checklist(
        campaign_id="SEB8008-20260525",
        env="LIVE",
        identity_id=820,
        repo_root=repo,
    )
    cli = c.cli_invocation_abs(repo)
    assert f"{cli} get-campaign" not in text
    assert f"{cli} get-dispatch-context" not in text
    assert "persist-initial-outreach-draft" in text
    assert "kol_bridge_tool.py" in cli


def test_blocks_redirect_on_get_campaign():
    c = _contract()
    bad = (
        "/Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli "
        "get-campaign --campaign-id X --env LIVE > /tmp/c.json"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "redirect_bridge_read_stdout" for h in hits)


def test_lint_write_facts_reply_draft():
    c = _contract()
    bad = 'kol_bridge_tool.py write-facts-multi --json \'{"namespaces":{"approval":{"approval.reply_draft":{}}}}\''
    hits = c.lint_agent_bridge_snippet(bad)
    assert any(h["code"] == "write_facts_reply_draft" for h in hits)
