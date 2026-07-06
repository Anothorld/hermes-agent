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

# ── Edit-memory bypass detection ──────────────────────────────────────────
# When the operator sends a reply via Console, send_reply.py checks whether
# the operator edited the AI draft (draft_source == "operator_edit") and, if
# so, launches an edit-memory gateway run that retains factual product/policy
# corrections to Hindsight.
#
# However, not every "operator_edit" session represents a genuine edit of the
# AI draft. In practice, many operators use the Console send path to send a
# completely different reply (live chat follow-up, escalation-time manual
# reply, independent decision based on internal info). The draft_source is
# still "operator_edit" because the operator touched the composer, but the
# content similarity between AI draft and operator reply is near zero.
#
# Launching edit-memory on these bypass cases wastes compute and risks
# extracting spurious "corrections" from an unrelated diff. We gate on a
# similarity threshold: if the plain-text similarity between the AI baseline
# and the operator's sent draft falls below BYPASS_SIMILARITY_THRESHOLD,
# the edit-memory run is skipped.
#
# The 0.15 threshold is empirically derived from two days of data
# (2026-07-01/02): similarity clusters bimodally — either ≥95% (directly
# adopted) or ≤10% (complete bypass). The 15%–65% range is essentially empty.
BYPASS_SIMILARITY_THRESHOLD = 0.15


def _strip_html(html: str) -> str:
    """Minimal HTML-to-plain-text for similarity comparison."""
    import re
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _text_similarity(a: str, b: str) -> float:
    """Ratio similarity between two plain-text strings (case-insensitive)."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


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
    # force_fresh=True bypasses the 15s messages cache so the just-sent
    # outbound message is visible immediately (not deferred to TTL expiry).
    message_id = ""
    try:
        msgs = fetch_messages(quickcep_session_id=quickcep_session_id, force_fresh=True)
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

    # Sending changes QuickCEP session state (new outbound, possible tag/lifecycle
    # bumps). Drop the L2 caches for this session so the FE's post-send reload
    # sees fresh messages/tags/orders instead of a stale 15s/60s/300s entry.
    try:
        from .quickcep_live import invalidate_cache
        invalidate_cache(quickcep_session_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.debug("send-reply cache invalidate failed session=%s: %s", quickcep_session_id, exc)

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
    #
    # Bypass gate (2026-07-03): skip edit-memory when the operator's sent draft
    # bears no resemblance to the AI baseline (similarity < BYPASS_SIMILARITY_THRESHOLD).
    # In these cases the operator sent a completely independent reply (live chat
    # follow-up, escalation manual reply, etc.) — the diff is meaningless and
    # would risk extracting spurious "corrections" from unrelated content.
    edit_memory_outcome = None
    if sess.get("draft_source") == "operator_edit":
        try:
            from .gateway_client import GatewayClient

            ctx = cal.get_dispatch_context(quickcep_session_id=quickcep_session_id, env=env) or {}
            facts = (ctx.get("facts") or {}) if isinstance(ctx, dict) else {}
            ai_baseline = ((facts.get("edit_memory") or {}).get("ai_baseline_html") or "") if isinstance(facts, dict) else ""
            if ai_baseline and ai_baseline != draft_html:
                similarity = _text_similarity(
                    _strip_html(ai_baseline), _strip_html(draft_html)
                )
                if similarity < BYPASS_SIMILARITY_THRESHOLD:
                    log.info(
                        "skip edit-memory: similarity %.0f%% (operator bypass) session=%s",
                        similarity * 100, quickcep_session_id,
                    )
                    cal.write_event(
                        quickcep_session_id=quickcep_session_id,
                        env=env,
                        event_type="edit_memory_skipped_bypass",
                        payload={"similarity": round(similarity, 4)},
                    )
                else:
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
