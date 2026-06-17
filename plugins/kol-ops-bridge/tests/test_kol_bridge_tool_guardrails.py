"""Tests for kol_bridge_tool CLI guardrails and compatibility shim."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
CLI = SCRIPTS / "kol_bridge_tool.py"
SHIM = PLUGIN_ROOT / "kol_bridge_tool.py"


def _load_guardrails():
    path = SCRIPTS / "_cli_guardrails.py"
    spec = importlib.util.spec_from_file_location("_cli_guardrails_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_preflight_rejects_get_escalation_campaign_id():
    g = _load_guardrails()
    with pytest.raises(SystemExit) as exc:
        g.preflight_argv(
            ["get-escalation", "--campaign-id", "SEB8008", "--escalation-id", "108"],
        )
    assert exc.value.code == 2


def test_preflight_rejects_bare_id_flag():
    g = _load_guardrails()
    with pytest.raises(SystemExit) as exc:
        g.preflight_argv(["get-identity", "--id", "806", "--env", "LIVE"])
    assert exc.value.code == 2


def test_preflight_rejects_wrong_nox_path():
    g = _load_guardrails()
    with pytest.raises(SystemExit) as exc:
        g.preflight_argv(
            [
                "python3",
                "plugins/kol-ops-bridge/scripts/nox_kol_tool.py",
                "contacts",
                "--env",
                "LIVE",
            ],
        )
    assert exc.value.code == 2


def test_lint_detects_wrong_nox_path():
    g = _load_guardrails()
    hits = g.lint_agent_bridge_snippet(
        "python3 plugins/kol-ops-bridge/scripts/nox_kol_tool.py contacts",
    )
    assert any(h["code"] == "wrong_nox_tool_path" for h in hits)


def test_cli_missing_subcommand_includes_hint(capsys):
    proc = subprocess.run(
        [sys.executable, str(CLI)],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "required: cmd" in proc.stderr or "required: cmd" in proc.stdout
    assert "Hint:" in proc.stderr
    assert "scripts/kol_bridge_tool.py" in proc.stderr


def test_cli_get_escalation_rejects_campaign_id_flag():
    proc = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "get-escalation",
            "--campaign-id",
            "SEB8008",
            "--escalation-id",
            "108",
        ],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stderr.strip().splitlines()[-1])
    assert payload["error"] == "invalid_cli_args"
    assert "list-escalations" in payload["hint"]


def test_lint_agent_code_detects_curl_and_hardcoded_key():
    g = _load_guardrails()
    bad = (
        'BRIDGE_KEY = "secret"\n'
        'subprocess.run(["curl", "-H", "X-Bridge-Key: x", '
        '"http://127.0.0.1:8080/api/plugins/kol-ops-bridge/identities/1/email-conversation"])\n'
    )
    hits = g.lint_agent_bridge_snippet(bad)
    codes = {h["code"] for h in hits}
    assert "hardcoded_bridge_key" in codes
    assert "curl_bridge_http" in codes


def test_cli_lint_agent_code_strict_exit():
    proc = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "lint-agent-code",
            "--snippet",
            'open("plugin_api.py").read()',
            "--strict",
        ],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert any(v["code"] == "read_plugin_source" for v in payload["violations"])


def test_cli_get_email_conversation_help():
    proc = subprocess.run(
        [sys.executable, str(CLI), "get-email-conversation", "--help"],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--operator-user-id" in proc.stdout
    assert "--campaign-id" in proc.stdout


def test_root_shim_forwards_to_scripts_cli():
    import os

    env = os.environ.copy()
    env["KOL_BRIDGE_TOOL_QUIET_SHIM"] = "1"
    proc = subprocess.run(
        [sys.executable, str(SHIM), "--help"],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    assert "get-escalation" in proc.stdout
