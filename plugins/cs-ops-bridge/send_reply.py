"""Console send-reply (PR1.6).

Reads the CAL-stored draft and sends it via ``quickcep_cli send-email`` as a
**service-initiated** call (bypassing the agent send-guard via a scoped
subprocess env), then backfills the outbound message_id and applies the
operator_sent handoff so the session lifecycle + escalations close.

This is the single outbound write path for the Console workbench. The agent
itself never calls send-email (still guard-blocked); only this service path,
gated by the Bridge key, is sanctioned to send.
"""

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
from .quickcep_live import fetch_messages
from .session_handoff import handle_operator_send

log = logging.getLogger(__name__)

SEND_EMAIL_SUBPROCESS_TIMEOUT = 150


def _quickcep_cli_path() -> Path:
    return quickcep_skill_dir() / "scripts" / "quickcep_cli.py"


def _run_send_email(
    *,
    cli: Path,
    session_id: str,
    subject: str,
    body: str,
    chat_session_id: Optional[str],
    attachments_json: Optional[str],
) -> tuple[int, str, str]:
    """Run quickcep_cli send-email with a scoped env that authorizes the service send.

    ``CS_OPS_ALLOW_QUICKCEP_SEND=1`` bypasses the agent send-guard for this
    sanctioned service call only (the subprocess env does not leak back to the
    parent process). PR0 refines this to a dedicated Bridge account env.
    """
    argv = [
        sys.executable, str(cli), "send-email", session_id,
        "--subject", subject,
        "--body", body,
    ]
    if chat_session_id:
        argv.extend(["--chat-session-id", chat_session_id])
    if attachments_json:
        argv.extend(["--attachments", attachments_json])
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=SEND_EMAIL_SUBPROCESS_TIMEOUT,
        env={**os.environ, "CS_OPS_ALLOW_QUICKCEP_SEND": "1"},
    )
    return proc.returncode, proc.stdout, proc.stderr


def _guard_draft(content: str, attachments_json: Optional[str]) -> Optional[dict[str, Any]]:
    """Run the shared draft guard (PR1.9). Returns a block payload or None."""
    from .draft_guard import guard_draft_content

    return guard_draft_content(content, attachments_json)


def send_reply(
    *,
    quickcep_session_id: str,
    env: str = "LIVE",
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    subject_override: Optional[str] = None,
) -> dict[str, Any]:
    """Send the CAL-stored draft and close the session lifecycle (PR1.6)."""
    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return {"ok": False, "error": "session not found"}
    draft_html = sess.get("draft_html")
    if not draft_html:
        return {"ok": False, "error": "no_draft", "error_detail": "no draft stored in CAL for this session"}

    attachments = []
    raw_att = sess.get("draft_attachments")
    if raw_att:
        try:
            attachments = json.loads(raw_att) or []
        except (json.JSONDecodeError, TypeError):
            attachments = []
    attachments_json = json.dumps(attachments, ensure_ascii=False) if attachments else None

    block = _guard_draft(draft_html, attachments_json)
    if block:
        return {"ok": False, "error": "guard_blocked", "error_detail": block}

    subject = subject_override or sess.get("email_subject") or ""
    chat_session_id = sess.get("chat_session_id") or ""

    cli = _quickcep_cli_path()
    if not cli.exists():
        return {"ok": False, "error": f"quickcep_cli not found: {cli}"}
    code, out, err = _run_send_email(
        cli=cli,
        session_id=quickcep_session_id,
        subject=subject,
        body=draft_html,
        chat_session_id=chat_session_id or None,
        attachments_json=attachments_json,
    )
    if code != 0:
        log.warning("send-reply quickcep_cli failed session=%s: %s", quickcep_session_id, err or out)
        return {"ok": False, "error": "send_failed", "exit_code": code,
                "stderr": err, "stdout": out}

    # Backfill the outbound message_id by reading the latest messages.
    message_id = ""
    try:
        msgs = fetch_messages(quickcep_session_id=quickcep_session_id)
        items = msgs.get("messages") or []
        if items:
            # Latest outbound = last operator/html message in chronological order.
            message_id = str(items[-1].get("id") or "")
    except Exception as exc:  # noqa: BLE001 — best-effort backfill
        log.warning("send-reply message backfill failed session=%s: %s", quickcep_session_id, exc)

    # Apply the operator_sent handoff (lifecycle + escalation close).
    info = {
        "chatSubSessionId": quickcep_session_id,
        "id": message_id,
        "chatSessionId": chat_session_id,
        "ownerId": f"console:{operator_id}" if operator_id else "console",
        "email_subject": subject,
    }
    handoff_result = handle_operator_send(info, env=env)

    cal.write_event(
        quickcep_session_id=quickcep_session_id,
        env=env,
        event_type="operator_draft_sent",
        payload={
            "operator_id": operator_id,
            "operator_name": operator_name,
            "message_id": message_id,
            "subject": subject,
            "attachments": len(attachments),
        },
    )

    # PR3: if the operator edited the AI draft, launch an edit-memory run that
    # inherits the reply context and retains product/policy corrections to
    # Hindsight. Guard-locked to hindsight tools via run_kind=edit_memory.
    edit_memory_outcome = None
    if sess.get("draft_source") == "operator_edit":
        try:
            from .gateway_client import GatewayClient

            ctx = cal.get_dispatch_context(quickcep_session_id=quickcep_session_id, env=env) or {}
            facts = (ctx.get("facts") or {}) if isinstance(ctx, dict) else {}
            ai_baseline = ((facts.get("edit_memory") or {}).get("ai_baseline_html") or "") if isinstance(facts, dict) else ""
            if ai_baseline and ai_baseline != draft_html:
                outcome = GatewayClient.from_env().start_edit_memory_run(
                    quickcep_session_id=quickcep_session_id,
                    env=env,
                    ai_draft_html=ai_baseline,
                    operator_draft_html=draft_html,
                    operator_id=operator_id or "",
                )
                edit_memory_outcome = {
                    "run_id": outcome.run_id,
                    "dedup_skipped": outcome.dedup_skipped,
                }
                cal.write_event(
                    quickcep_session_id=quickcep_session_id,
                    env=env,
                    event_type="edit_memory_run_launched",
                    payload={"run_id": outcome.run_id, "operator_id": operator_id},
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, never block the send
            log.warning("edit-memory run launch failed session=%s: %s", quickcep_session_id, exc)
            edit_memory_outcome = {"error": str(exc)[:200]}

    # Clear the CAL draft now that the reply has been sent (draft_html/ai_baseline
    # for the edit_memory run were already captured above / live in cs_facts, so
    # this does not affect the launched run). Prevents a workbench reload from
    # re-showing the just-sent content in the composer.
    try:
        cal.clear_draft(quickcep_session_id=quickcep_session_id, env=env)
    except Exception as exc:  # noqa: BLE001
        log.warning("clear_draft after send failed session=%s: %s", quickcep_session_id, exc)

    result = {
        "ok": True,
        "session_id": quickcep_session_id,
        "message_id": message_id,
        "handoff": handoff_result,
        "send_stdout": out,
    }
    if edit_memory_outcome is not None:
        result["edit_memory"] = edit_memory_outcome
    return result
