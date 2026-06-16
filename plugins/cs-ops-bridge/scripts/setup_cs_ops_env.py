#!/usr/bin/env python3
"""Generate cs-ops-bridge secrets and print profile .env lines.

Usage::

    python plugins/cs-ops-bridge/scripts/setup_cs_ops_env.py
    python plugins/cs-ops-bridge/scripts/setup_cs_ops_env.py --write-profile-env
"""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path

SECRETS_PATH = Path.home() / ".hermes/cs-ops-bridge/secrets.yaml"
PROFILE_ENV = Path.home() / ".hermes/profiles/povison-cs/.env"

ENV_BLOCK = """
# =============================================================================
# POVISON CS OPS (cs-ops-bridge + gateway)
# =============================================================================
HERMES_CS_OPS_BRIDGE_KEY={key}
CS_OPS_BRIDGE_BASE=http://127.0.0.1:8081/api/plugins/cs-ops-bridge
CS_OPS_GATEWAY_BASE=http://127.0.0.1:8643
CS_OPS_ENV=LIVE
CS_OPS_QUICKCEP_WATCHER_AUTO_START=true
CS_OPS_FEISHU_POLLER_AUTO_START=true
CS_OPS_ESCALATION_TIMEOUT_AUTO_START=true
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup cs-ops-bridge env")
    parser.add_argument(
        "--write-profile-env",
        action="store_true",
        help="Append CS_OPS block to povison-cs profile .env if missing",
    )
    args = parser.parse_args()

    key = secrets.token_urlsafe(32)
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_PATH.write_text(f"bridge_key: {key}\n", encoding="utf-8")
    print(f"Wrote bridge key to {SECRETS_PATH}")

    block = ENV_BLOCK.format(key=key).strip()
    print("\nAdd to profile .env (or use --write-profile-env):\n")
    print(block)

    if args.write_profile_env:
        existing = PROFILE_ENV.read_text(encoding="utf-8") if PROFILE_ENV.exists() else ""
        if "HERMES_CS_OPS_BRIDGE_KEY" in existing:
            print(f"\n{PROFILE_ENV} already contains HERMES_CS_OPS_BRIDGE_KEY — skipped append")
            return
        PROFILE_ENV.parent.mkdir(parents=True, exist_ok=True)
        with PROFILE_ENV.open("a", encoding="utf-8") as fh:
            fh.write("\n" + block + "\n")
        print(f"\nAppended CS_OPS block to {PROFILE_ENV}")


if __name__ == "__main__":
    main()
