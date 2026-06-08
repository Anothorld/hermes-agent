"""CLI errors must surface on STDOUT (POVISON 'terminal no output' regression).

The Hermes agent only sees the terminal tool's stdout. When the bridge CLI
wrote errors to stderr only, a malformed command looked like empty output
(``{"output": "", "exit_code": 2}``) and the agent abandoned the CLI for
ad-hoc ``execute_code``. These tests pin every failure path to stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_CLI = _PLUGIN_ROOT / "scripts" / "kol_bridge_tool.py"


def _run(args: list[str], env_extra: dict[str, str] | None = None):
    import os
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        cwd=_PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _stdout_json(proc) -> dict:
    line = proc.stdout.strip().splitlines()[-1]
    return json.loads(line)


def test_unknown_subcommand_error_on_stdout():
    proc = _run(["frobnicate", "--env", "LIVE"])
    assert proc.returncode == 2
    assert proc.stdout.strip(), "error must be visible on stdout, not stderr-only"
    payload = _stdout_json(proc)
    assert payload["error"] == "invalid_cli_args"


def test_guardrail_rejection_on_stdout():
    # get-escalation + --campaign-id is rejected by preflight (_die_cli).
    proc = _run(["get-escalation", "--escalation-id", "1", "--campaign-id", "X"])
    assert proc.returncode == 2
    payload = _stdout_json(proc)
    assert payload["error"] == "invalid_cli_args"
    assert "rejected_flag" in payload


def test_bridge_unreachable_on_stdout():
    proc = _run(
        ["get-identity", "--identity-id", "1", "--env", "LIVE"],
        env_extra={"HERMES_KOL_OPS_BRIDGE_BASE": "http://127.0.0.1:9/none"},
    )
    assert proc.returncode == 2
    payload = _stdout_json(proc)
    assert payload["error"] == "bridge_unreachable"


def test_persist_missing_body_field_on_stdout(tmp_path: Path):
    body = tmp_path / "p.json"
    body.write_text(json.dumps({
        "identity_id": 1,
        "campaign_id": "C",
        "child_skill": "kol-cold-outreach",
        "child_envelope": {"subject": "s", "to": "x@y.com"},  # body missing
    }))
    proc = _run(["persist-initial-outreach-draft", "--env", "LIVE", "--json", f"@{body}"])
    assert proc.returncode == 2
    payload = _stdout_json(proc)
    assert payload["error"] == "invalid_cli_args"
    assert "child_envelope.body" in payload["hint"]
