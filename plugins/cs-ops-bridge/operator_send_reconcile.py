"""REST backfill for operator_sent handoff when SIO operatorSendMsg is unavailable."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from . import cal
from .profile_refs import quickcep_skill_dir
from .session_handoff import handle_operator_send

log = logging.getLogger(__name__)

_SYNC_STATUSES = frozenset({"draft_ready", "awaiting_expert", "processing"})


def _quickcep_scripts_dir() -> Path:
    return Path(os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", str(quickcep_skill_dir())))


def _load_quickcep_credentials_from_profile() -> None:
    """Load QUICKCEP_* into os.environ for subprocess CLI when missing."""
    if os.environ.get("QUICKCEP_EMAIL") and os.environ.get("QUICKCEP_PASSWORD"):
        return
    try:
        from profile_refs import cs_profile_dir

        env_path = cs_profile_dir() / ".env"
        if not env_path.exists():
            return
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key not in ("QUICKCEP_EMAIL", "QUICKCEP_PASSWORD"):
                continue
            val = val.strip().strip("'").strip('"')
            if val and not os.environ.get(key):
                os.environ[key] = val
    except Exception as exc:
        log.debug("operator reconcile: could not load QUICKCEP credentials: %s", exc)


def _quickcep_subprocess_env() -> dict[str, str]:
    _load_quickcep_credentials_from_profile()
    return {k: v for k, v in os.environ.items() if isinstance(v, str)}


def _fetch_last_operator_message(quickcep_session_id: str) -> Optional[dict[str, Any]]:
    """Return the newest operator outbound message from QuickCEP, if any."""
    cli = _quickcep_scripts_dir() / "scripts" / "quickcep_cli.py"
    if not cli.exists():
        return None
    proc = subprocess.run(
        [
            sys.executable,
            str(cli),
            "messages",
            quickcep_session_id,
            "--page-size",
            "30",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_quickcep_scripts_dir()),
        env=_quickcep_subprocess_env(),
    )
    if proc.returncode != 0:
        snippet = (proc.stderr or proc.stdout or "").strip()[:300]
        log.warning(
            "operator reconcile: quickcep_cli messages failed session=%s rc=%s: %s",
            quickcep_session_id,
            proc.returncode,
            snippet,
        )
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning(
            "operator reconcile: invalid JSON from messages session=%s: %s",
            quickcep_session_id,
            (proc.stdout or "").strip()[:200],
        )
        return None
    records = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return None
    for msg in records:
        if str(msg.get("ownerType") or "") != "operator":
            continue
        msg_id = str(msg.get("id") or "").strip()
        if not msg_id:
            continue
        return {
            "id": msg_id,
            "createTime": msg.get("createTime") or msg.get("time") or "",
            "chatSubSessionId": quickcep_session_id,
        }
    return None


def reconcile_operator_sent_once(*, env: str | None = None) -> dict[str, Any]:
    """Sync CAL operator_sent for sessions where QuickCEP shows operator already replied."""
    env = env or os.environ.get("CS_OPS_ENV", "LIVE")
    checked = 0
    synced = 0
    skipped = 0
    seen_row_ids: set[int] = set()
    for status in sorted(_SYNC_STATUSES):
        for sess in cal.list_sessions(env=env, status=status, limit=200):
            row_id = int(sess["id"])
            if row_id in seen_row_ids:
                continue
            seen_row_ids.add(row_id)
            if cal.session_has_event(session_row_id=row_id, event_type="operator_sent"):
                skipped += 1
                continue
            checked += 1
            sid = str(sess.get("quickcep_session_id") or "")
            if not sid:
                continue
            op_msg = _fetch_last_operator_message(sid)
            if not op_msg:
                continue
            result = handle_operator_send(
                {
                    "chatSubSessionId": sid,
                    "chatSessionId": sess.get("chat_session_id") or "",
                    "id": op_msg["id"],
                    "channel": "email",
                },
                env=env,
            )
            if result.get("ok") and not result.get("skipped"):
                synced += 1
                log.info("operator reconcile synced session=%s msg=%s", sid, op_msg["id"])
            elif result.get("skipped"):
                log.info(
                    "operator reconcile skipped session=%s reason=%s",
                    sid,
                    result.get("reason"),
                )
    return {"checked": checked, "synced": synced, "skipped_already": skipped}
