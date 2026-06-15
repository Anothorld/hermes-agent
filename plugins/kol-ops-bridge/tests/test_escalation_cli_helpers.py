"""Tests for open-escalation / persist-reply-draft CLI normalizers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
CLI = SCRIPTS / "kol_bridge_tool.py"


def _import_helpers():
    sys.path.insert(0, str(SCRIPTS))
    import _escalation_cli_helpers as mod  # noqa: WPS433

    return mod


def test_normalize_open_escalation_rule_id_to_reason():
    mod = _import_helpers()
    body = {"rule_id": "budget_over_cap", "goal_name": "compensation_negotiation"}
    mod.normalize_open_escalation_body(body)
    assert body["reason"] == "budget_over_cap"
    assert body["goal"] == "compensation_negotiation"


def test_maybe_attach_linked_escalation_id_single_open():
    mod = _import_helpers()
    client = MagicMock()
    client.request.return_value = {"escalations": [{"id": 42, "state": "awaiting_answer"}]}
    body = {
        "identity_id": 698,
        "campaign_id": "POVISON-TS-8319-20260603",
        "env": "LIVE",
    }
    assert mod.maybe_attach_linked_escalation_id(client, body) is None
    assert body["linked_escalation_id"] == 42


def test_open_escalation_accepts_identity_campaign_flags():
    """SKILL documents --identity-id/--campaign-id on open-escalation."""
    proc = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "open-escalation",
            "--env",
            "TEST",
            "--identity-id",
            "698",
            "--campaign-id",
            "POVISON-TS-8319-20260603",
            "--json",
            json.dumps(
                {
                    "rule_id": "test_rule",
                    "question_to_operator": "测试问题",
                },
            ),
            "--bridge-key",
            "dry-run-invalid-key-for-argparse-only",
        ],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    # Should pass argparse + require_keys(reason via rule_id alias); HTTP may fail.
    assert "unrecognized arguments" not in (proc.stderr + proc.stdout)
    assert "json_missing_field" not in (proc.stderr + proc.stdout)
