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


def test_lint_write_facts_reply_draft():
    c = _contract()
    bad = 'kol_bridge_tool.py write-facts-multi --json \'{"namespaces":{"approval":{"approval.reply_draft":{}}}}\''
    hits = c.lint_agent_bridge_snippet(bad)
    assert any(h["code"] == "write_facts_reply_draft" for h in hits)
