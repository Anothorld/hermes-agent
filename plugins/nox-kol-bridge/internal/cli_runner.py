"""Subprocess wrapper for ``noxinfluencer`` CLI (or TEST fixtures)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Optional

from internal.paths import fixtures_root


class NoxCliError(RuntimeError):
    """CLI failed or returned error envelope."""

    def __init__(self, message: str, *, envelope: Optional[dict] = None) -> None:
        super().__init__(message)
        self.envelope = envelope


class NoxInsufficientCreditError(NoxCliError):
    """Supplier rejected call for insufficient credits — do not cache or commit."""


_INSUFFICIENT_CODES = frozenset(
    {
        "INSUFFICIENT_CREDIT",
        "INSUFFICIENT_CREDITS",
        "QUOTA_EXCEEDED",
        "CREDIT_EXHAUSTED",
    }
)


def _nox_bin() -> str:
    return os.environ.get("NOXINFLUENCER_BIN", "noxinfluencer")


def _parse_envelope(stdout: str) -> dict[str, Any]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise NoxCliError(f"invalid JSON from noxinfluencer: {exc}") from exc
    if not isinstance(envelope, dict):
        raise NoxCliError("noxinfluencer returned non-object JSON")
    code = envelope.get("error_code")
    if envelope.get("success") is False and code in _INSUFFICIENT_CODES:
        raise NoxInsufficientCreditError(
            f"nox insufficient credit: {code}",
            envelope=envelope,
        )
    if envelope.get("success") is False:
        raise NoxCliError(
            f"nox API error: {code or envelope.get('summary') or 'unknown'}",
            envelope=envelope,
        )
    return envelope


def run_cli(
    args: list[str],
    *,
    env_mode: str,
    lang: str = "en",
    timeout: int = 120,
    stdin_json: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run ``noxinfluencer`` with ``-j`` and parse JSON stdout."""
    if env_mode.upper() == "TEST":
        raise NoxCliError("run_cli should not be called in TEST; use fixture_loader")

    from internal.nox_auth import ensure_nox_auth

    ensure_nox_auth(env_mode)

    cmd = [_nox_bin(), "-j", "--lang", lang, *args]
    env = os.environ.copy()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
        input=json.dumps(stdin_json) if stdin_json is not None else None,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        if proc.stdout.strip():
            try:
                env_err = json.loads(proc.stdout)
                code = env_err.get("error_code") if isinstance(env_err, dict) else None
                if code in _INSUFFICIENT_CODES:
                    raise NoxInsufficientCreditError(detail, envelope=env_err)
            except json.JSONDecodeError:
                pass
        raise NoxCliError(f"noxinfluencer exit {proc.returncode}: {detail}")
    return _parse_envelope(proc.stdout)


def load_fixture(name: str) -> dict[str, Any]:
    path = fixtures_root() / name
    if not path.exists():
        raise NoxCliError(f"fixture missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def doctor() -> dict[str, Any]:
    if shutil.which(_nox_bin()) is None:
        return {"ok": False, "error": "noxinfluencer not on PATH"}
    try:
        proc = subprocess.run(
            [_nox_bin(), "doctor", "-j"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return json.loads(proc.stdout) if proc.stdout else {"ok": proc.returncode == 0}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def run_creator_search(
    platform: str,
    body: dict[str, Any],
    *,
    env_mode: str,
    lang: str = "en",
) -> dict[str, Any]:
    """POST creator search with JSON body via ``--body-file -``."""
    payload = dict(body)
    payload.setdefault("platform", platform)
    return run_cli(
        ["creator", "search", "--platform", platform, "--body-file", "-"],
        env_mode=env_mode,
        lang=lang,
        stdin_json=payload,
    )


def run_monitor_create(
    project_name: str,
    *,
    env_mode: str,
    lang: str = "en",
) -> dict[str, Any]:
    """Create monitor project (mutation; requires ``--force``)."""
    return run_cli(
        [
            "monitor",
            "create",
            "--project_name",
            project_name,
            "--force",
        ],
        env_mode=env_mode,
        lang=lang,
    )


def run_monitor_add_task(
    *,
    project_id: int,
    video_url: str,
    env_mode: str,
    lang: str = "en",
    monitor_days: Optional[int] = None,
) -> dict[str, Any]:
    """Add video to monitor project (mutation; requires ``--force``)."""
    args = [
        "monitor",
        "add-task",
        "--project_id",
        str(project_id),
        "--video_url",
        video_url,
        "--force",
    ]
    if monitor_days is not None:
        args.extend(["--monitor_days", str(monitor_days)])
    return run_cli(args, env_mode=env_mode, lang=lang)


def extract_project_id(create_envelope: dict[str, Any]) -> int:
    """Parse ``project_id`` from monitor create response."""
    data = create_envelope.get("data") if isinstance(create_envelope, dict) else None
    if not isinstance(data, dict):
        raise NoxCliError("monitor create missing data envelope")
    raw = data.get("project_id") or data.get("id")
    if raw is None:
        raise NoxCliError("monitor create response missing project_id")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise NoxCliError(f"invalid project_id: {raw!r}") from exc


def quota(env_mode: str, *, lang: str = "en", use_cache: bool = True) -> dict[str, Any]:
    if env_mode.upper() == "TEST":
        return {
            "success": True,
            "data": {"remaining": 9999, "mode": "TEST_fixture"},
        }
    from internal.quota_remote import read_cached_quota, store_cached_quota

    if use_cache:
        cached = read_cached_quota()
        if cached is not None:
            return {**cached, "_quota_from_cache": True}
    envelope = run_cli(["quota"], env_mode=env_mode, lang=lang)
    store_cached_quota(envelope)
    return envelope
