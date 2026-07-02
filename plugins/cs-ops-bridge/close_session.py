"""Close a QuickCEP chat session from the Console workbench.

Uses ``quickcep_cli leave-chat`` (Socket.io + batchLeaveChat) to emit
``chat_end`` on the QuickCEP side, then optionally applies CAL ``reviewed``
handoff so the bridge lifecycle stays in sync.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from . import cal
from .profile_refs import quickcep_skill_dir
from .quickcep_leave_confirm import reconcile_leave_chat_payload
from .session_handoff import apply_handoff

log = logging.getLogger(__name__)

LEAVE_CHAT_SUBPROCESS_TIMEOUT = 120


def _quickcep_cli_path() -> Path:
    return quickcep_skill_dir() / "scripts" / "quickcep_cli.py"


def _parse_cli_json(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_stdout": text[:500]}


def close_session(
    *,
    quickcep_session_id: str,
    env: str = "LIVE",
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    mark_reviewed: bool = True,
    note: str = "",
) -> dict[str, Any]:
    """End the QuickCEP session and optionally mark CAL reviewed."""
    cli = _quickcep_cli_path()
    if not cli.is_file():
        return {"ok": False, "error": "quickcep_cli_not_found", "path": str(cli)}

    proc = subprocess.run(
        [sys.executable, str(cli), "leave-chat", quickcep_session_id],
        capture_output=True,
        text=True,
        timeout=LEAVE_CHAT_SUBPROCESS_TIMEOUT,
    )
    payload = _parse_cli_json(proc.stdout)
    payload = reconcile_leave_chat_payload(
        payload,
        cli=cli,
        session_id=quickcep_session_id,
    )
    if proc.returncode != 0 or not payload.get("ok"):
        err = payload.get("error") or proc.stderr.strip() or f"exit {proc.returncode}"
        log.warning(
            "leave-chat failed session=%s code=%s err=%s",
            quickcep_session_id,
            proc.returncode,
            err,
        )
        return {
            "ok": False,
            "error": "quickcep_close_failed",
            "error_detail": err,
            "quickcep": payload,
            "stderr": (proc.stderr or "")[:500],
        }

    reviewed: Optional[dict[str, Any]] = None
    if mark_reviewed:
        sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
        if not sess:
            return {
                "ok": True,
                "quickcep": payload,
                "reviewed": None,
                "warning": "session_not_in_cal",
            }
        if str(sess.get("status") or "") != "reviewed":
            op_label = (operator_name or operator_id or "Console").strip()
            hint = note.strip() or "操作员在工单台结束 QuickCEP 工单"
            reviewed = apply_handoff(
                quickcep_session_id=quickcep_session_id,
                phase="reviewed",
                env=env,
                context={
                    "actions_taken": f"{op_label} 结束 QuickCEP 工单",
                    "operator_hint": hint,
                    "customer_need": note.strip(),
                },
            )
            if not reviewed.get("ok"):
                log.warning(
                    "QuickCEP closed but reviewed handoff failed session=%s: %s",
                    quickcep_session_id,
                    reviewed.get("error"),
                )
                return {
                    "ok": True,
                    "quickcep": payload,
                    "reviewed": reviewed,
                    "warning": "reviewed_handoff_failed",
                }

    cal.write_event(
        quickcep_session_id=quickcep_session_id,
        event_type="console_close_session",
        payload={
            "operator_id": operator_id,
            "operator_name": operator_name,
            "mark_reviewed": mark_reviewed,
        },
        env=env,
    )
    return {"ok": True, "quickcep": payload, "reviewed": reviewed}
