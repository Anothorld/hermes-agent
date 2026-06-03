"""Ensure ``@noxinfluencer/cli`` auth before LIVE API calls.

The upstream CLI reads ``api_key`` from ``~/.noxinfluencer/config.json`` only
(see ``requireConfig`` in ``@noxinfluencer/cli``). ``NOXINFLUENCER_API_KEY`` in
Hermes ``.env`` is **not** consumed at runtime unless persisted via ``auth``.

This module hydrates the env var from profile ``.env`` files and can bootstrap
``noxinfluencer auth --key-stdin`` automatically before LIVE subprocess calls.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class NoxAuthError(RuntimeError):
    """Nox CLI is not authenticated for LIVE calls."""


def _nox_bin() -> str:
    return os.environ.get("NOXINFLUENCER_BIN", "noxinfluencer")


def _nox_config_path() -> Path:
    override = os.environ.get("NOXINFLUENCER_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".noxinfluencer" / "config.json"


def read_stored_config() -> dict[str, Any]:
    """Return parsed ``~/.noxinfluencer/config.json`` (empty dict if missing)."""
    path = _nox_config_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def has_stored_api_key() -> bool:
    key = read_stored_config().get("api_key")
    return isinstance(key, str) and bool(key.strip())


def _candidate_hermes_env_files() -> list[Path]:
    """Profile-aware ``.env`` paths that may hold ``NOXINFLUENCER_API_KEY``."""
    homes: list[Path] = []
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        homes.append(Path(hermes_home).expanduser())

    default_home = Path.home() / ".hermes"
    if not homes or homes[-1] != default_home:
        homes.append(default_home)

    orchestrator = default_home / "profiles" / "kol-orchestrator"
    if orchestrator.is_dir():
        homes.append(orchestrator)

    active = default_home / "active_profile"
    try:
        profile = active.read_text(encoding="utf-8").strip()
    except OSError:
        profile = ""
    if profile and profile != "default" and "/" not in profile:
        profile_home = default_home / "profiles" / profile
        if profile_home.is_dir():
            homes.append(profile_home)

    seen: set[Path] = set()
    files: list[Path] = []
    for home in homes:
        path = (home / ".env").resolve()
        if path not in seen:
            files.append(path)
            seen.add(path)
    return files


def _load_key_from_env_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if not line.startswith("NOXINFLUENCER_API_KEY="):
            continue
        value = line.split("=", 1)[1].strip().strip("'\"")
        return value if value else None
    return None


def resolve_env_api_key(*, hydrate: bool = True) -> str | None:
    """Return ``NOXINFLUENCER_API_KEY`` from env or Hermes ``.env`` files."""
    direct = os.environ.get("NOXINFLUENCER_API_KEY", "").strip()
    if direct:
        return direct
    if not hydrate:
        return None
    for path in _candidate_hermes_env_files():
        key = _load_key_from_env_file(path)
        if key:
            os.environ["NOXINFLUENCER_API_KEY"] = key
            return key
    return None


def bootstrap_auth_from_env() -> bool:
    """Persist env key into ``~/.noxinfluencer/config.json`` via CLI ``auth``."""
    api_key = resolve_env_api_key()
    if not api_key:
        return False
    if has_stored_api_key():
        return True
    if shutil.which(_nox_bin()) is None:
        return False
    proc = subprocess.run(
        [_nox_bin(), "auth", "--key-stdin"],
        input=f"{api_key}\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise NoxAuthError(
            f"noxinfluencer auth failed: {detail or proc.returncode}"
        )
    return has_stored_api_key()


def _hermes_home_display() -> str:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return raw.replace(str(Path.home()), "~", 1)
    return "~/.hermes"


def ensure_nox_auth(env_mode: str) -> None:
    """Raise :class:`NoxAuthError` when LIVE cannot call Nox API."""
    if env_mode.upper() != "LIVE":
        return
    if has_stored_api_key():
        return
    if bootstrap_auth_from_env():
        return
    if resolve_env_api_key(hydrate=False):
        raise NoxAuthError(
            "NOXINFLUENCER_API_KEY is set but could not configure "
            "noxinfluencer CLI — install @noxinfluencer/cli and ensure "
            f"{_nox_bin()} is on PATH"
        )
    raise NoxAuthError(
        "Not authenticated. Register at https://www.noxinfluencer.com/skills, "
        f"add NOXINFLUENCER_API_KEY to {_hermes_home_display()}/.env, "
        "then run: python plugins/nox-kol-bridge/scripts/nox_kol_tool.py "
        "doctor --env LIVE"
    )


def auth_status(*, env: str = "LIVE") -> dict[str, Any]:
    """Preflight snapshot for ``nox_kol_tool doctor``."""
    env_upper = env.upper()
    cli_on_path = shutil.which(_nox_bin()) is not None
    stored = has_stored_api_key()
    env_key = resolve_env_api_key(hydrate=True)
    config_path = str(_nox_config_path())
    env_files_checked = [str(p) for p in _candidate_hermes_env_files()]

    if env_upper == "TEST":
        return {
            "ok": True,
            "env": env_upper,
            "mode": "TEST_fixtures",
            "cli_on_path": cli_on_path,
            "config_path": config_path,
            "stored_api_key": stored,
            "env_api_key": bool(env_key),
        }

    ok = cli_on_path and (stored or bool(env_key))
    detail = None
    if not cli_on_path:
        detail = f"{_nox_bin()} not on PATH — npm install -g @noxinfluencer/cli"
    elif not stored and not env_key:
        detail = (
            f"No API key in {config_path} and NOXINFLUENCER_API_KEY not in "
            f"process env or Hermes .env ({', '.join(env_files_checked)})"
        )
    elif not stored and env_key:
        detail = "env key present; will auto-run noxinfluencer auth on first LIVE call"

    return {
        "ok": ok,
        "env": env_upper,
        "cli_on_path": cli_on_path,
        "config_path": config_path,
        "stored_api_key": stored,
        "env_api_key": bool(env_key),
        "env_files_checked": env_files_checked,
        "detail": detail,
        "register_url": "https://www.noxinfluencer.com/skills",
    }


def classify_auth_error(message: str) -> str | None:
    """Map CLI stderr/stdout to ``NOX_AUTH_MISSING`` when applicable."""
    lower = message.lower()
    if "auth_required" in lower or "not authenticated" in lower:
        return "NOX_AUTH_MISSING"
    return None
