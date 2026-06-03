#!/usr/bin/env python3
"""One-shot setup after upgrading to per-operator Gmail.

1. Apply Console DB schema (``init_db`` — includes poller watermark tables).
2. Reset + sync ``kol-reply-dispatcher`` skill into Hermes homes.
3. Print environment checklist for bridge / poller hosts.

Usage:
  python hermes-agent/playground/kol-ops-console/scripts/gmail_multimailbox_setup.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
CONSOLE_ROOT = SCRIPT.parents[1]
BACKEND = CONSOLE_ROOT / "backend"
AGENT_PRJ = SCRIPT.parents[4]
HERMES_AGENT = AGENT_PRJ / "hermes-agent"
SKILLS = HERMES_AGENT / "skills"
SYNC_SCRIPT = AGENT_PRJ / "playground" / "learning" / "sync_skills.py"


def _init_console_db() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.db import init_db  # noqa: WPS433

    init_db()
    print("✓ Console init_db() applied")


def _reset_reply_dispatcher_skill() -> dict:
    os.environ.setdefault("HERMES_BUNDLED_SKILLS", str(SKILLS))
    sys.path.insert(0, str(HERMES_AGENT))
    from tools.skills_sync import reset_bundled_skill  # noqa: WPS433

    homes = [
        Path.home() / ".hermes",
        Path.home() / ".hermes" / "profiles" / "kol-orchestrator",
    ]
    last: dict = {}
    for home in homes:
        os.environ["HERMES_HOME"] = str(home)
        last = reset_bundled_skill("kol-reply-dispatcher", restore=True)
        print(f"✓ reset kol-reply-dispatcher @ {home}: {last.get('action')}")
    return last


def _sync_all_skills() -> int:
    if not SYNC_SCRIPT.is_file():
        print(f"WARN: {SYNC_SCRIPT} missing — skip full skill sync", file=sys.stderr)
        return 0
    print(f"\n=== running {SYNC_SCRIPT} ===")
    proc = subprocess.run([sys.executable, str(SYNC_SCRIPT)], check=False)
    return int(proc.returncode)


def _print_checklist() -> None:
    bridge_example = HERMES_AGENT / "plugins" / "kol-ops-bridge" / ".env.example"
    console_example = CONSOLE_ROOT / ".env.example"
    print("\n=== Environment checklist ===")
    print("Console (.env): see", console_example)
    print("Bridge/poller (.env): see", bridge_example)
    print(
        json.dumps(
            {
                "required": [
                    "KOC_CONSOLE_BASE",
                    "KOC_INTERNAL_API_KEY or HERMES_KOL_OPS_BRIDGE_KEY",
                    "KOC_GMAIL_TOKENS_DIR",
                    "KOC_GOOGLE_CLIENT_SECRET_PATH (Console OAuth)",
                ],
                "optional": [
                    "KOC_GMAIL_TOKEN_SECRET",
                    "KOC_DEFAULT_OPERATOR_USER_ID",
                    "KOC_GMAIL_CONNECTION_CACHE_SEC",
                ],
                "post_setup": [
                    "Restart KOL Ops Console (applies init_db on boot)",
                    "Each operator: Settings → Connect Gmail",
                    "Restart kol_reply_dispatcher after bridge env is set",
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def main() -> int:
    rc = 0
    try:
        _init_console_db()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR init_db: {exc}", file=sys.stderr)
        rc = 1
    try:
        _reset_reply_dispatcher_skill()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR skill reset: {exc}", file=sys.stderr)
        rc = 1
    rc = max(rc, _sync_all_skills())
    _print_checklist()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
