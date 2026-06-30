"""Shared bridge key resolution (env, secrets file, profile .env)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_SECRETS_PATH = Path(os.path.expanduser("~/.hermes/cs-ops-bridge/secrets.yaml"))


def _read_key_from_secrets_file() -> Optional[str]:
    if not _SECRETS_PATH.is_file():
        return None
    for raw in _SECRETS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("bridge_key:"):
            val = line.split(":", 1)[1].strip().strip("'\"")
            return val or None
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() == "bridge_key":
                return v.strip().strip("'\"") or None
    return None


def _read_key_from_profile_env() -> Optional[str]:
    try:
        plugin_root = Path(__file__).resolve().parent
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from profile_refs import cs_profile_dir  # noqa: WPS433

        env_path = cs_profile_dir() / ".env"
        if not env_path.is_file():
            return None
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            for name in ("HERMES_CS_OPS_BRIDGE_KEY", "CS_OPS_BRIDGE_KEY"):
                if line.startswith(f"{name}="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    return val or None
    except Exception:
        return None
    return None


def load_bridge_key() -> Optional[str]:
    """Resolve bridge HMAC key from env, secrets.yaml, or profile .env."""
    for name in ("HERMES_CS_OPS_BRIDGE_KEY", "CS_OPS_BRIDGE_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    from_file = _read_key_from_secrets_file()
    if from_file:
        return from_file
    return _read_key_from_profile_env()


def require_bridge_key_bytes() -> bytes:
    key = load_bridge_key()
    if not key:
        raise ValueError(
            "HERMES_CS_OPS_BRIDGE_KEY not set (env, ~/.hermes/cs-ops-bridge/secrets.yaml, or profile .env)"
        )
    return key.encode("utf-8")
