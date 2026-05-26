"""Bridge-key provisioning for gateway-spawned KOL agent runs."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from fastapi import HTTPException, status


BRIDGE_KEY_ENV = "HERMES_KOL_OPS_BRIDGE_KEY"
BRIDGE_KEY_ALIASES: tuple[str, ...] = (
    BRIDGE_KEY_ENV,
    "KOC_BRIDGE_KEY",
    "HERMES_KOL_BRIDGE_KEY",
    "BRIDGE_KEY",
)

_SAFE_ENV_VALUE = re.compile(r"^[A-Za-z0-9_./:@%+=,\-]+$")
_PLACEHOLDER_KEYS = frozenset({"replace-with-bridge-key", "change-me"})


class _SettingsLike(Protocol):
    bridge_key: str


def _clean_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().strip("'\"")
    if not cleaned or "\n" in cleaned or "\r" in cleaned:
        return None
    if cleaned.lower() in _PLACEHOLDER_KEYS:
        return None
    return cleaned


def _load_key_from_kv_file(path: Path, keys: tuple[str, ...]) -> str | None:
    if not path.exists():
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
        separator = "=" if "=" in line else ":" if ":" in line else None
        if separator is None:
            continue
        key, value = line.split(separator, 1)
        if key.strip() in keys:
            resolved = _clean_key(value)
            if resolved:
                return resolved
    return None


def _console_env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _bridge_secrets_path() -> Path:
    return Path.home() / ".hermes/kol-ops-bridge/secrets.yaml"


def resolve_bridge_key(settings: _SettingsLike | None = None) -> str | None:
    """Resolve the bridge key from console config, env aliases, or files."""
    if settings is None:
        from .config import get_settings

        settings = get_settings()
    candidates = [getattr(settings, "bridge_key", "")]
    candidates.extend(os.environ.get(name, "") for name in BRIDGE_KEY_ALIASES)
    for candidate in candidates:
        key = _clean_key(candidate)
        if key:
            return key
    return (
        _load_key_from_kv_file(_bridge_secrets_path(), ("bridge_key",))
        or _load_key_from_kv_file(_console_env_path(), BRIDGE_KEY_ALIASES)
    )


def _active_profile_home(default_home: Path) -> Path | None:
    active_path = default_home / "active_profile"
    try:
        profile = active_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not profile or profile == "default" or "/" in profile:
        return None
    profile_home = default_home / "profiles" / profile
    return profile_home if profile_home.exists() else None


def _candidate_env_files() -> list[Path]:
    homes: list[Path] = []
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        homes.append(Path(hermes_home).expanduser())

    default_home = Path.home() / ".hermes"
    homes.append(default_home)

    active_home = _active_profile_home(default_home)
    if active_home is not None:
        homes.append(active_home)

    orchestrator_home = default_home / "profiles" / "kol-orchestrator"
    if orchestrator_home.exists():
        homes.append(orchestrator_home)

    env_files: list[Path] = []
    seen: set[Path] = set()
    for home in homes:
        path = (home / ".env").resolve()
        if path not in seen:
            env_files.append(path)
            seen.add(path)
    return env_files


def _format_env_value(value: str) -> str:
    if _SAFE_ENV_VALUE.fullmatch(value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _is_env_assignment(line: str, name: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return False
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").lstrip()
    return stripped.startswith(f"{name}=")


def _upsert_env_var(path: Path, name: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []

    rendered = f"{name}={_format_env_value(value)}"
    replaced = False
    for index, line in enumerate(lines):
        if _is_env_assignment(line, name):
            lines[index] = rendered
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(rendered)

    fd, tmp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def ensure_gateway_bridge_key(settings: _SettingsLike | None = None) -> str:
    """Make the bridge key visible to Gateway runs, or fail before launch."""
    key = resolve_bridge_key(settings)
    if not key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "error": "missing_bridge_key",
                "missing": BRIDGE_KEY_ENV,
                "message": (
                    "HERMES_KOL_OPS_BRIDGE_KEY is not available for "
                    "gateway-spawned KOL agent runs. Set KOC_BRIDGE_KEY "
                    "or HERMES_KOL_OPS_BRIDGE_KEY, then retry the approval."
                ),
            },
        )

    os.environ[BRIDGE_KEY_ENV] = key
    os.environ.setdefault("KOC_BRIDGE_KEY", key)

    written = 0
    failures: list[str] = []
    for env_file in _candidate_env_files():
        try:
            _upsert_env_var(env_file, BRIDGE_KEY_ENV, key)
            written += 1
        except OSError as exc:
            failures.append(f"{env_file}: {exc}")
    if written == 0:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            {
                "error": "bridge_key_injection_failed",
                "message": "Could not write HERMES_KOL_OPS_BRIDGE_KEY to any Hermes .env file.",
                "paths": failures,
            },
        )
    return key
