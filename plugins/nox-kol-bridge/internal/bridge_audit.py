"""Optional CAL audit events via ``kol_bridge_tool.py write-event``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def _bridge_tool_path() -> Optional[Path]:
    env = os.environ.get("KOL_BRIDGE_TOOL")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    here = Path(__file__).resolve()
    candidate = here.parents[2] / "kol-ops-bridge" / "scripts" / "kol_bridge_tool.py"
    if not candidate.is_file() and len(here.parents) > 3:
        candidate = here.parents[3] / "kol-ops-bridge" / "scripts" / "kol_bridge_tool.py"
    return candidate if candidate.is_file() else None


def write_nox_event(
    *,
    event_type: str,
    identity_id: int,
    campaign_id: str,
    env: str,
    payload: dict[str, Any],
    actor: str = "nox_kol_tool",
) -> Optional[dict[str, Any]]:
    """Best-effort ``write-event``; returns bridge JSON or None if skipped."""
    tool = _bridge_tool_path()
    if tool is None:
        return None
    body = {
        "env": env,
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "event_type": event_type,
        "actor": actor,
        "payload": payload,
    }
    cmd = [
        sys.executable,
        str(tool),
        "write-event",
        "--env",
        env,
        "--identity-id",
        str(identity_id),
        "--campaign-id",
        campaign_id,
        "--event-type",
        event_type,
        "--actor",
        actor,
        "--json",
        json.dumps(body, ensure_ascii=False),
    ]
    key = os.environ.get("HERMES_KOL_OPS_BRIDGE_KEY") or os.environ.get("KOC_BRIDGE_KEY")
    if key:
        cmd.extend(["--bridge-key", key])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout) if proc.stdout.strip() else {"ok": True}
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None
