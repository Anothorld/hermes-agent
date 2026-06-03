"""CLI entry smoke tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nox_kol_tool.py"


def test_cache_stats_cli(nox_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(nox_home))
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "cache-stats"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert "cache" in data
    assert "usage" in data


def test_doctor_test_env():
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "doctor", "--env", "TEST"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["env"] == "TEST"
