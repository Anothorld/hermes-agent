#!/usr/bin/env python3
"""Generate cs-ops-bridge secrets and print profile .env lines.

Usage::

    python plugins/cs-ops-bridge/scripts/setup_cs_ops_env.py
    python plugins/cs-ops-bridge/scripts/setup_cs_ops_env.py --write-profile-env
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
from profile_refs import cs_profile_dir, cs_profile_name  # noqa: E402

SECRETS_PATH = Path.home() / ".hermes/cs-ops-bridge/secrets.yaml"
PROFILE_ENV = cs_profile_dir() / ".env"

ENV_VARS: dict[str, str] = {
    "CS_OPS_PROFILE": "",  # filled at runtime
    "HERMES_CS_OPS_BRIDGE_KEY": "",  # filled at runtime
    "CS_OPS_BRIDGE_BASE": "http://127.0.0.1:8081/api/plugins/cs-ops-bridge",
    "CS_OPS_GATEWAY_BASE": "http://127.0.0.1:8643",
    "CS_OPS_ENV": "LIVE",
    "CS_OPS_QUICKCEP_WATCHER_AUTO_START": "true",
    "CS_OPS_FEISHU_POLLER_AUTO_START": "true",
    "CS_OPS_ESCALATION_TIMEOUT_AUTO_START": "true",
    "CS_OPS_FEISHU_ESCALATION_CHAT_ID": "oc_0cdfe1f385b7e839bc147fd99915fe91",
    "API_SERVER_ENABLED": "true",
    "API_SERVER_PORT": "8643",
}

FEISHU_KEYS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_DOMAIN",
    "FEISHU_CONNECTION_MODE",
    "FEISHU_ALLOW_ALL_USERS",
    "FEISHU_GROUP_POLICY",
)

ENV_BLOCK_HEADER = """
# =============================================================================
# POVISON CS OPS (cs-ops-bridge + gateway) — profile: {profile}
# =============================================================================
"""


def _read_key_from_secrets() -> str | None:
    if not SECRETS_PATH.exists():
        return None
    for raw in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("bridge_key:"):
            val = line.split(":", 1)[1].strip().strip("'\"")
            return val or None
    return None


def _ensure_bridge_key() -> str:
    existing = _read_key_from_secrets()
    if existing:
        return existing
    key = secrets.token_urlsafe(32)
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_PATH.write_text(f"bridge_key: {key}\n", encoding="utf-8")
    print(f"Wrote new bridge key to {SECRETS_PATH}")
    return key


def _format_env_block(*, key: str, profile: str) -> str:
    values = dict(ENV_VARS)
    values["CS_OPS_PROFILE"] = profile
    values["HERMES_CS_OPS_BRIDGE_KEY"] = key
    lines = [ENV_BLOCK_HEADER.format(profile=profile).rstrip()]
    for k, v in values.items():
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def _upsert_env_var(text: str, name: str, value: str) -> tuple[str, bool]:
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
    line = f"{name}={value}"
    if pattern.search(text):
        new_text, n = pattern.subn(line, text, count=1)
        return new_text, n > 0
    sep = "" if not text or text.endswith("\n") else "\n"
    return text + sep + line + "\n", True


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip("'\"")
        out[key.strip()] = val
    return out


def _inherit_feishu_from(profile_name: str) -> dict[str, str]:
    src = Path.home() / ".hermes" / "profiles" / profile_name / ".env"
    src_vars = _read_env_file(src)
    return {k: src_vars[k] for k in FEISHU_KEYS if src_vars.get(k)}


def sync_profile_env(*, key: str, profile: str, inherit_feishu_from: str | None = None) -> None:
    """Append or update CS_OPS vars in profile .env without clobbering other keys."""
    existing = PROFILE_ENV.read_text(encoding="utf-8") if PROFILE_ENV.exists() else ""
    updated = existing
    changed = False
    values = dict(ENV_VARS)
    values["CS_OPS_PROFILE"] = profile
    values["HERMES_CS_OPS_BRIDGE_KEY"] = key
    if inherit_feishu_from:
        inherited = _inherit_feishu_from(inherit_feishu_from)
        if not inherited:
            print(f"WARN: no FEISHU_* vars found in profile {inherit_feishu_from}")
        else:
            print(f"Inheriting {len(inherited)} FEISHU vars from {inherit_feishu_from}")
    else:
        inherited = {}
    if "POVISON CS OPS" not in updated:
        updated = updated.rstrip("\n") + _format_env_block(key=key, profile=profile)
        changed = True
    else:
        for name, val in values.items():
            updated, did = _upsert_env_var(updated, name, val)
            changed = changed or did
    for name, val in inherited.items():
        if f"{name}=" not in updated:
            updated, did = _upsert_env_var(updated, name, val)
            changed = changed or did
    missing_feishu = [k for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET") if f"{k}=" not in updated]
    if missing_feishu:
        print(
            f"WARN: profile .env still missing {', '.join(missing_feishu)} — "
            "Feishu escalation notify and poller will not work until set."
        )
    if not changed:
        print(f"\n{PROFILE_ENV} already up to date for CS_OPS vars")
        return
    PROFILE_ENV.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_ENV.write_text(updated, encoding="utf-8")
    print(f"\nSynced CS_OPS block to {PROFILE_ENV}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup cs-ops-bridge env")
    parser.add_argument(
        "--write-profile-env",
        action="store_true",
        help=f"Sync CS_OPS vars into {cs_profile_name()} profile .env",
    )
    parser.add_argument(
        "--inherit-feishu-from",
        default=None,
        metavar="PROFILE",
        help="Copy FEISHU_APP_ID/SECRET from another profile ONLY if it is the same bot in AI客服后援 (NOT kol-orchestrator/KOL合作智能助手)",
    )
    args = parser.parse_args()

    key = _ensure_bridge_key()
    if SECRETS_PATH.exists() and _read_key_from_secrets() == key:
        print(f"Using bridge key from {SECRETS_PATH}")

    profile = cs_profile_name()
    block = _format_env_block(key=key, profile=profile).strip()
    print("\nProfile .env CS_OPS block:\n")
    print(block)

    if args.write_profile_env:
        sync_profile_env(key=key, profile=profile, inherit_feishu_from=args.inherit_feishu_from)


if __name__ == "__main__":
    main()
