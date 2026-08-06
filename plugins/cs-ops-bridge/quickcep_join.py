"""QuickCEP ``joinChat`` helper shared by watcher / relaunch / legacy draft-save.

Centralizes the retry + fail-soft / fail-hard policy so callers do not
duplicate subprocess plumbing. The two entry points differ in posture:

- ``watcher`` / ``relaunch`` (``source="launch"`` / ``"relaunch"``):
  fail-soft, default **1 attempt** — QuickCEP slowness must not block the
  inbound launch path. Failure is recorded as a CAL event + WARNING and the
  gateway run proceeds; the eventual ``send-email`` (Console / Autopilot)
  still joins as a fallback.

- legacy ``draft-save`` (``source="draft_save"``):
  fail-hard, **3 attempts** — preserves the existing legacy QuickCEP
  draft-save contract (exit non-zero on failure so the agent surfaces it).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional

# Support both package-relative import (watcher / plugin_api) and top-level
# import (cs_bridge_tool script context, which puts _PLUGIN_ROOT on sys.path).
try:
    from .profile_refs import quickcep_skill_dir
except ImportError:  # pragma: no cover — script context
    from profile_refs import quickcep_skill_dir  # type: ignore[no-redef]

log = logging.getLogger(__name__)

# Subprocess budget per attempt: getUserInfo (45s) + joinChat (60s) + margin.
JOIN_CHAT_SUBPROCESS_TIMEOUT = 130
JOIN_CHAT_BACKOFF_BASE_S = 2.0

# Default attempt counts per source. Watcher/relaunch default to a single
# attempt so a slow QuickCEP cannot stall the inbound launch path; legacy
# draft-save keeps the historical 3-attempt fail-hard policy.
_DEFAULT_MAX_ATTEMPTS: dict[str, int] = {
    "launch": 1,
    "relaunch": 1,
    "draft_save": 3,
}

_ENV = os.environ.get("CS_OPS_ENV", "LIVE")


class JoinChatError(RuntimeError):
    """Raised by ``join_chat_session`` when ``raise_on_failure=True``."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(payload.get("error_detail") or payload.get("error") or "join-chat failed")


def _quickcep_cli_path() -> Path:
    return quickcep_skill_dir() / "scripts" / "quickcep_cli.py"


def _run_quickcep_cli(argv: list[str], *, timeout: int = JOIN_CHAT_SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run ``quickcep_cli.py`` with the bridge plugin dir exported.

    Mirrors the env that ``cs_bridge_tool._run_quickcep_cli`` and
    ``session_handoff._run_quickcep_cli`` set so QuickCEP JWT / store env is
    consistent across callers.
    """
    cli = _quickcep_cli_path()
    plugin_root = str(Path(__file__).resolve().parent)
    env = os.environ.copy()
    env.setdefault("CS_OPS_BRIDGE_PLUGIN_DIR", plugin_root)
    return subprocess.run(
        [sys.executable, str(cli), *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cli.parent.parent),
        env=env,
    )


def _parse_payload(stdout: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"stdout": stdout}


def _error_is_retryable(payload: dict[str, Any], proc: subprocess.CompletedProcess[str]) -> bool:
    """Retry only on HTTP/socket timeouts (transient QuickCEP slowness)."""
    err = str(payload.get("error") or "")
    step = str(payload.get("failed_step") or "")
    blob = f"{err} {step} {proc.stdout} {proc.stderr}".lower()
    return "timed out" in blob or "timeout" in blob


def _build_failure_payload(
    session_id: str,
    proc: subprocess.CompletedProcess[str],
    payload: dict[str, Any],
    *,
    attempt: int,
    max_attempts: int,
    source: str,
) -> dict[str, Any]:
    failed_step = payload.get("failed_step")
    err = str(payload.get("error") or "join-chat failed")
    error_detail: Optional[str] = None
    if failed_step:
        if "timed out" in err.lower():
            error_detail = f"{failed_step} timed out (QuickCEP HTTP)"
        else:
            error_detail = f"{failed_step} failed: {err}"
    elif "timed out" in err.lower():
        error_detail = "join-chat timed out (QuickCEP HTTP)"
    return {
        "ok": False,
        "source": source,
        "session_id": session_id,
        "result_code": payload.get("result_code"),
        "attempts": attempt,
        "max_attempts": max_attempts,
        "error": err,
        "error_detail": error_detail,
        "failed_step": failed_step,
        "exit_code": proc.returncode,
        "stderr": proc.stderr,
        "raw": payload,
    }


def _success_payload(
    session_id: str,
    payload: dict[str, Any],
    *,
    attempt: int,
    source: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "source": source,
        "session_id": session_id,
        "result_code": payload.get("result_code"),
        "attempts": attempt,
        "error": None,
        "error_detail": None,
        "failed_step": None,
        "raw": payload,
    }


def join_chat_session(
    session_id: str,
    *,
    max_attempts: Optional[int] = None,
    raise_on_failure: bool = False,
    source: str = "launch",
) -> dict[str, Any]:
    """Call QuickCEP ``joinChat`` for ``session_id``.

    Args:
        session_id: QuickCEP chatSubSessionId.
        max_attempts: Override default attempt count for ``source``.
        raise_on_failure: When True (legacy draft-save), on terminal failure
            print the failure JSON and ``sys.exit`` with the subprocess exit
            code, preserving the legacy CLI contract. When False (watcher /
            relaunch), return a ``{ok: False, ...}`` dict without raising.
        source: ``"launch"`` | ``"relaunch"`` | ``"draft_save"`` — drives
            default attempts and is recorded in the result + CAL event.

    Returns:
        Dict with ``ok``, ``source``, ``session_id``, ``attempts``,
        ``result_code``, ``error``, ``error_detail``, ``failed_step``.
    """
    if max_attempts is None:
        max_attempts = _DEFAULT_MAX_ATTEMPTS.get(source, 1)
    last_failure: Optional[dict[str, Any]] = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            time.sleep(JOIN_CHAT_BACKOFF_BASE_S * (2 ** (attempt - 2)))
        proc = _run_quickcep_cli(["join-chat", session_id])
        payload = _parse_payload(proc.stdout)
        if proc.returncode == 0 and payload.get("result_code") in (200, None) and not payload.get("failed_step"):
            return _success_payload(session_id, payload, attempt=attempt, source=source)
        last_failure = _build_failure_payload(
            session_id, proc, payload, attempt=attempt, max_attempts=max_attempts, source=source,
        )
        if attempt < max_attempts and _error_is_retryable(payload, proc):
            continue
        break

    failure = last_failure or {
        "ok": False,
        "source": source,
        "session_id": session_id,
        "attempts": max_attempts,
        "max_attempts": max_attempts,
        "error": "join-chat failed",
        "error_detail": None,
        "failed_step": None,
    }

    if raise_on_failure:
        # Preserve legacy cs_bridge_tool contract: print + exit non-zero.
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        sys.exit(failure.get("exit_code", 1) if isinstance(failure.get("exit_code"), int) else 1)
    return failure


def join_chat_on_launch_enabled() -> bool:
    """Toggle for the watcher / relaunch join path (default ON)."""
    return os.environ.get("CS_OPS_JOIN_CHAT_ON_LAUNCH", "1").strip().lower() not in ("0", "false", "no", "off")


def launch_join_max_attempts() -> int:
    """Override attempt count for the launch / relaunch path (default 1)."""
    raw = os.environ.get("CS_OPS_LAUNCH_JOIN_MAX_ATTEMPTS", "").strip()
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return n
        except ValueError:
            log.warning("invalid CS_OPS_LAUNCH_JOIN_MAX_ATTEMPTS=%r, defaulting to 1", raw)
    return _DEFAULT_MAX_ATTEMPTS["launch"]


def record_join_chat_event(
    *,
    quickcep_session_id: str,
    join_result: Mapping[str, Any],
    message_id: Optional[str] = None,
    env: Optional[str] = None,
) -> None:
    """Persist a ``quickcep_join_chat`` CAL event + log line for observability.

    Never raises — failure to record the audit event must not break the
    inbound launch path.
    """
    if env is None:
        env = _ENV
    ok = bool(join_result.get("ok"))
    source = str(join_result.get("source") or "launch")
    attempts = int(join_result.get("attempts") or 0)
    if ok:
        log.info(
            "quickcep join ok session=%s source=%s attempts=%d",
            quickcep_session_id,
            source,
            attempts,
        )
        log.info(
            "cs.join session=%s env=%s source=%s decision=joined attempts=%d "
            "result_code=%s message_id=%s",
            quickcep_session_id, env, source, attempts,
            join_result.get("result_code"), message_id,
        )
    else:
        log.warning(
            "quickcep join failed session=%s source=%s error=%s detail=%s",
            quickcep_session_id,
            source,
            join_result.get("error"),
            join_result.get("error_detail"),
        )
        log.info(
            "cs.join session=%s env=%s source=%s decision=failed attempts=%d "
            "error=%s failed_step=%s",
            quickcep_session_id, env, source, attempts,
            join_result.get("error"), join_result.get("failed_step"),
        )
    payload = {
        "ok": ok,
        "source": source,
        "attempts": attempts,
        "result_code": join_result.get("result_code"),
        "error": join_result.get("error"),
        "error_detail": join_result.get("error_detail"),
        "failed_step": join_result.get("failed_step"),
        "message_id": message_id,
    }
    try:
        try:
            from . import cal
        except ImportError:  # pragma: no cover — script context
            import cal  # type: ignore[no-redef]
        cal.write_event(
            quickcep_session_id=quickcep_session_id,
            event_type="quickcep_join_chat",
            payload=payload,
            env=env,
        )
    except Exception as exc:
        log.warning("quickcep_join_chat event write failed session=%s: %s", quickcep_session_id, exc)
