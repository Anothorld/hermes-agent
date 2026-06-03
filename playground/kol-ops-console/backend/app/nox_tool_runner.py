"""Invoke ``nox_kol_tool.py`` deterministically from Console backend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[4]


def nox_tool_script() -> Path:
    return _REPO_ROOT / "plugins" / "nox-kol-bridge" / "scripts" / "nox_kol_tool.py"


def resolve_hermes_home() -> str:
    """Profile-aware ``HERMES_HOME`` for Nox cache / quota ledger paths."""
    explicit = os.environ.get("HERMES_HOME", "").strip()
    if explicit:
        return explicit
    orchestrator = Path.home() / ".hermes" / "profiles" / "kol-orchestrator"
    if orchestrator.is_dir():
        return str(orchestrator)
    return str(Path.home() / ".hermes")


def run_nox_tool(argv: list[str], *, timeout: int = 180) -> dict[str, Any]:
    """Run nox CLI subcommand; return parsed JSON stdout."""
    script = nox_tool_script()
    if not script.is_file():
        raise FileNotFoundError(f"nox_kol_tool not found: {script}")
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", resolve_hermes_home())
    proc = subprocess.run(
        [sys.executable, str(script), *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        try:
            err = json.loads(proc.stdout)
        except json.JSONDecodeError:
            err = {"success": False, "detail": detail, "exit_code": proc.returncode}
        else:
            err["exit_code"] = proc.returncode
        return err
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid nox tool JSON: {exc}") from exc
