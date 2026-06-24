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


def test_allows_single_ingest_confirmed_candidate_terminal():
    c = _contract()
    good = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "ingest-confirmed-candidate --campaign-id SSF8033-20260609 --env LIVE "
        "--json @/tmp/ingest_dressyourdecor.json"
    )
    hits = c.lint_agent_bridge_snippet(good)
    assert not any(h["code"] == "batch_ingest_files" for h in hits)
    assert not any(h["code"] == "terminal_multi_ingest" for h in hits)


def test_blocks_terminal_multi_ingest_semicolon():
    c = _contract()
    bad = (
        "python3 -u /abs/kol_bridge_tool.py ingest-confirmed-candidate --env LIVE "
        "--json @/tmp/ingest_a.json; "
        "python3 -u /abs/kol_bridge_tool.py ingest-confirmed-candidate --env LIVE "
        "--json @/tmp/ingest_b.json"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "terminal_multi_ingest" for h in hits)


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


def test_approval_checklist_skips_discover_when_queued():
    c = _contract()
    text = c.approval_cli_checklist(
        campaign_id="C1",
        env="LIVE",
        identity_ids=[101, 102],
        email_discovery_queued_ids=[101],
    )
    assert "pending_email_discovery" in text
    assert text.count("delegate kol-email-discovery") == 0


def test_approval_checklist_skips_brief_when_queued():
    c = _contract()
    text = c.approval_cli_checklist(
        campaign_id="C1",
        env="LIVE",
        identity_ids=[201],
        creator_brief_queued_ids=[201],
    )
    assert "pending_creator_brief" in text
    assert "kol-creator-brief-loader in this outreach run" in text


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
    assert any(h["code"] == "redirect_bridge_stdout" for h in hits)


def test_blocks_redirect_on_list_candidates():
    c = _contract()
    bad = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "list-candidates --campaign-id SSF8033-20260609 --env LIVE > /tmp/candidates.json"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "redirect_bridge_stdout" for h in hits)


def test_blocks_redirect_on_list_outreach_cooldown_handles():
    c = _contract()
    bad = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "list-outreach-cooldown-handles --env LIVE --plain > /tmp/cooldown.txt"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "redirect_bridge_stdout" for h in hits)


def test_blocks_redirect_on_print_agent_contract():
    c = _contract()
    bad = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "print-agent-contract > /tmp/contract.txt"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "redirect_bridge_stdout" for h in hits)


def test_blocks_pipe_head_on_list_candidates():
    c = _contract()
    bad = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "list-candidates --campaign-id X --env LIVE | head -20"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "pipe_bridge_stdout" for h in hits)


def test_allows_tee_on_list_candidates():
    c = _contract()
    ok = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "list-candidates --campaign-id X --env LIVE | tee /tmp/candidates.json"
    )
    hits = c.lint_terminal_command(ok)
    assert not hits


def test_allows_stderr_redirect_only_on_list_candidates():
    c = _contract()
    ok = (
        "python3 -u /Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
        "list-candidates --campaign-id SSF8033-20260609 --env LIVE 2>/dev/null"
    )
    hits = c.lint_terminal_command(ok)
    assert not any(h["code"] == "redirect_bridge_stdout" for h in hits)


def test_blocks_invalid_read_identity_subcommand():
    c = _contract()
    bad = (
        "python3 -u /abs/kol_bridge_tool.py read-identity --identity-id 1 --env LIVE"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "invalid_subcommand_read_identity" for h in hits)


def test_blocks_invalid_list_campaigns_subcommand():
    c = _contract()
    bad = "python3 -u /abs/kol_bridge_tool.py list-campaigns --env LIVE"
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "invalid_subcommand_list_campaigns" for h in hits)


def test_blocks_invalid_pretty_flag_position():
    c = _contract()
    bad = (
        "python3 -u /abs/kol_bridge_tool.py get-identity --identity-id 1 "
        "--env LIVE --pretty"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "invalid_cli_pretty_flag_position" for h in hits)


def test_blocks_nox_quota_snapshot_on_bridge_cli():
    c = _contract()
    bad = (
        "python3 -u /abs/kol_bridge_tool.py quota-snapshot --env LIVE"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "nox_subcommand_on_bridge_cli" for h in hits)


def test_blocks_invalid_plain_on_discovery_skip():
    c = _contract()
    bad = (
        "python3 -u /abs/kol_bridge_tool.py list-discovery-skip-handles --env LIVE --plain"
    )
    hits = c.lint_terminal_command(bad)
    assert any(h["code"] == "invalid_plain_on_discovery_skip" for h in hits)


def test_format_block_message_includes_guard_source():
    c = _contract()
    msg = c.format_block_message([{"code": "batch_ingest_files", "hint": "x"}])
    import json
    payload = json.loads(msg)
    assert payload["source"] == "kol_bridge_agent_guard"
    assert "note" in payload


def test_lint_write_facts_reply_draft():
    c = _contract()
    bad = 'kol_bridge_tool.py write-facts-multi --json \'{"namespaces":{"approval":{"approval.reply_draft":{}}}}\''
    hits = c.lint_agent_bridge_snippet(bad)
    assert any(h["code"] == "write_facts_reply_draft" for h in hits)


def test_memory_layers_brief_block():
    c = _contract()
    text = c.memory_layers_brief_block()
    assert "Memory layers" in text
    assert "learning_hints" in text
    assert "Hindsight" in text


def test_format_hindsight_recall_seed():
    c = _contract()
    text = c.format_hindsight_recall_seed(
        campaign_id="C-1",
        identity_id=99,
        handle="@kol",
    )
    assert "# hindsight_recall_seed" in text
    assert "campaign_id: C-1" in text
    assert "identity_id: 99" in text
    assert "handle: @kol" in text
