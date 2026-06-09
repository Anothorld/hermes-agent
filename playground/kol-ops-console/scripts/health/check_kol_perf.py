#!/usr/bin/env python3
"""Parse KOL orchestrator + bridge logs for performance health warnings."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def _default_agent_log() -> Path:
    return Path.home() / ".hermes" / "profiles" / "kol-orchestrator" / "logs" / "agent.log"


def _default_bridge_log() -> Path:
    return Path.home() / ".hermes" / "kol-ops-bridge" / "bridge.log"


def _tail_lines(path: Path, max_lines: int = 5000) -> list[str]:
    if not path.is_file():
        return []
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return data[-max_lines:]


def check_agent_log(lines: list[str]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    tab_acquired_at: dict[str, float] = {}
    now = time.time()
    for line in lines:
        if "Tab pool acquired" in line:
            tab_acquired_at["last"] = now
        if "browser_navigate completed" in line and tab_acquired_at.get("last"):
            tab_acquired_at.pop("last", None)
    if tab_acquired_at.get("last") and now - tab_acquired_at["last"] > 120:
        warnings.append({
            "code": "tab_acquired_no_navigate",
            "severity": "warn",
            "detail": "Tab acquired >120s ago with no browser_navigate completed",
        })
    devtools_hits = sum(1 for ln in lines if "mcp_chrome_devtools" in ln)
    if devtools_hits >= 3:
        warnings.append({
            "code": "chrome_devtools_errors",
            "severity": "warn",
            "detail": f"mcp_chrome_devtools errors: {devtools_hits} in tail",
        })
    iter_hits = [ln for ln in lines if re.search(r"api_calls=\s*9[0-9]/90", ln)]
    if iter_hits:
        warnings.append({
            "code": "run_iteration_budget",
            "severity": "warn",
            "detail": "At least one run hit ~90/90 iteration budget",
        })
    return warnings


def check_bridge_log(lines: list[str]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    db_errors = sum(
        1 for ln in lines if "unable to open database file" in ln
    )
    if db_errors >= 3:
        warnings.append({
            "code": "bridge_db_open_errors",
            "severity": "warn",
            "detail": f"unable to open database file: {db_errors} in tail",
            "action": "restart bridge; check ulimit -n and cal.db-wal permissions",
        })
    return warnings


def check_gateway_poll(lines: list[str]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    per_run: dict[str, int] = defaultdict(int)
    for line in lines:
        m = re.search(r'GET /v1/runs/([a-f0-9-]+)', line)
        if m:
            per_run[m.group(1)] += 1
    hot = {rid: c for rid, c in per_run.items() if c >= 20}
    if hot:
        warnings.append({
            "code": "gateway_poll_storm",
            "severity": "info",
            "detail": f"High GET /v1/runs counts in tail: {len(hot)} run(s)",
            "sample": dict(list(hot.items())[:5]),
        })
    return warnings


def main() -> int:
    p = argparse.ArgumentParser(description="KOL performance log health probe")
    p.add_argument("--agent-log", type=Path, default=None)
    p.add_argument("--bridge-log", type=Path, default=None)
    p.add_argument("--gateway-log", type=Path, default=None)
    args = p.parse_args()

    agent_log = args.agent_log or _default_agent_log()
    bridge_log = args.bridge_log or _default_bridge_log()
    gateway_log = args.gateway_log

    warnings: list[dict[str, Any]] = []
    warnings.extend(check_agent_log(_tail_lines(agent_log)))
    warnings.extend(check_bridge_log(_tail_lines(bridge_log)))
    if gateway_log and gateway_log.is_file():
        warnings.extend(check_gateway_poll(_tail_lines(gateway_log)))

    out = {
        "ok": not any(w.get("severity") == "warn" for w in warnings),
        "warnings": warnings,
        "paths": {
            "agent_log": str(agent_log),
            "bridge_log": str(bridge_log),
            "gateway_log": str(gateway_log) if gateway_log else None,
        },
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
