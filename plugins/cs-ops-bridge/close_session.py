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

_OUT_OF_SCOPE_CLOSE_NOTE_MARKERS = (
    "不在处理范围",
    "不在 AI 处理范围",
    "垃圾/无关",
    "spam_irrelevant",
)


def _should_close_escalations(*, close_escalations: bool, note: str) -> bool:
    """Return True when open escalations should be resolved with the ticket close.

    Explicit ``close_escalations=True`` (out-of-scope close bar) always wins. We
    also infer from the operator note so a console/backend version skew cannot
    leave orphan escalations when the close note clearly went through.
    """
    if close_escalations:
        return True
    text = (note or "").strip()
    return any(marker in text for marker in _OUT_OF_SCOPE_CLOSE_NOTE_MARKERS)


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
    close_escalations: bool = False,
) -> dict[str, Any]:
    """End the QuickCEP session and optionally mark CAL reviewed.

    When ``close_escalations`` is True (spam/irrelevant close flow), any open
    awaiting_answer / resuming escalations for this session are also resolved
    so the operator's single "关闭工单" action fully tears down the ticket.

    **Tag-before-close ordering (fixed 2026-07-17):** The reviewed handoff
    (which updates QuickCEP tags) is applied *before* ``leave-chat`` closes the
    session.  QuickCEP silently drops tag changes on sessions that have already
    received ``chat_end`` — the API returns HTTP 200 but does not persist the
    new tags.  Applying tags first ensures AI-已结案 is actually written.
    """
    cli = _quickcep_cli_path()
    if not cli.is_file():
        return {"ok": False, "error": "quickcep_cli_not_found", "path": str(cli)}

    # ── Step 1: Apply reviewed handoff (tags + note) WHILE the session is still open.
    #    QuickCEP does not persist tag changes after chat_end.
    reviewed: Optional[dict[str, Any]] = None
    in_cal = True
    if mark_reviewed:
        sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
        if not sess:
            in_cal = False
        elif str(sess.get("status") or "") != "reviewed":
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
                    "reviewed handoff failed before close session=%s: %s",
                    quickcep_session_id,
                    reviewed.get("error"),
                )
                # Proceed to close the session anyway — don't leave it open.

    # ── Step 2: Close the QuickCEP session (leave-chat → chat_end).
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
            "reviewed": reviewed,
            "stderr": (proc.stderr or "")[:500],
        }

    # If the session was never in CAL, return with a warning (but session is closed).
    if mark_reviewed and not in_cal:
        return {
            "ok": True,
            "quickcep": payload,
            "reviewed": None,
            "warning": "session_not_in_cal",
        }

    cal.write_event(
        quickcep_session_id=quickcep_session_id,
        event_type="console_close_session",
        payload={
            "operator_id": operator_id,
            "operator_name": operator_name,
            "mark_reviewed": mark_reviewed,
            "close_escalations": close_escalations,
            "close_escalations_effective": _should_close_escalations(
                close_escalations=close_escalations, note=note
            ),
        },
        env=env,
    )
    result: dict[str, Any] = {"ok": True, "quickcep": payload, "reviewed": reviewed}

    if _should_close_escalations(close_escalations=close_escalations, note=note):
        try:
            from .operator_escalation_close import close_escalations_on_operator_manual_reply

            esc_hint = note.strip() or "操作员关闭工单（垃圾/无关），升级随之关闭"
            esc_result = close_escalations_on_operator_manual_reply(
                quickcep_session_id=quickcep_session_id,
                env=env,
                operator_hint=esc_hint,
            )
            result["escalations_closed"] = esc_result.get("closed", [])
            if esc_result.get("skipped"):
                result["escalations_closed_skipped"] = esc_result.get("reason")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "close_escalations failed during close_session session=%s err=%s",
                quickcep_session_id,
                exc,
            )
            result["escalations_closed_error"] = str(exc)

    return result
