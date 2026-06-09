"""Process one inbound Gmail message — event, escalation, gateway dispatch."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Literal

from ..gmail_client import GmailClient, GmailMessage, GmailUnavailable
from ..mailbox_escalation import ensure_mailbox_mismatch_escalation
from .deps import BridgeRequestError, InboundDeps, MatchBridgeError
from .gateway_client import dispatcher_instructions
from .matcher import match_identity
from .payload import clip_text, mailbox_mismatch_signal, pending_reply_payload
from .schemas import ProcessStatus
from .state import register_console_run

log = logging.getLogger(__name__)

MailboxMismatchOutcome = Literal["none", "skip", "retry"]


def handle_mailbox_mismatch(
    *,
    identity_id: int,
    campaign_id: str | None,
    env: str,
    msg: GmailMessage,
    mailbox_email: str,
    mismatch: dict[str, Any],
) -> MailboxMismatchOutcome:
    if not mismatch.get("mailbox_mismatch") or not campaign_id:
        return "none"
    bound = str(mismatch.get("bound_mailbox_email") or "")
    detected = str(mismatch.get("detected_mailbox_email") or mailbox_email or "")
    if not bound or not detected:
        return "none"
    try:
        esc_id = ensure_mailbox_mismatch_escalation(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            bound_mailbox_email=bound,
            detected_mailbox_email=detected,
        )
        log.warning(
            "[mailbox_mismatch] msg=%s identity=%s bound=%s detected=%s escalation=%s",
            msg.message_id,
            identity_id,
            bound,
            detected,
            esc_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("mailbox mismatch escalation failed msg=%s: %s", msg.message_id, exc)
        return "retry"
    return "skip"


def process_message(
    msg: GmailMessage,
    env: str,
    *,
    client: GmailClient,
    deps: InboundDeps,
    mailbox_user_id: int = 0,
    mailbox_email: str = "",
) -> ProcessStatus:
    """Return whether the message was dispatched, skipped, or should retry."""
    bridge = deps.bridge
    try:
        matched = match_identity(msg, env=env, bridge=bridge)
    except MatchBridgeError as exc:
        log.error("[retry] msg=%s identity match bridge error: %s", msg.message_id, exc)
        return "retry"

    if not matched:
        log.info("[skip] msg=%s no identity match (from=%s)", msg.message_id, msg.from_addr)
        return "skipped"

    identity_id = matched.identity_id
    campaign_id = matched.campaign_id

    try:
        dispatch_status = (
            bridge.reply_dispatch_status(
                identity_id=identity_id,
                campaign_id=str(campaign_id or ""),
                message_id=msg.message_id,
                env=env,
            )
            if campaign_id
            else {}
        )
    except BridgeRequestError as exc:
        log.error("[retry] reply_dispatch_status failed msg=%s: %s", msg.message_id, exc)
        return "retry"

    if isinstance(dispatch_status, dict) and dispatch_status.get("should_skip_poller"):
        log.info(
            "[skip] msg=%s identity=%s already has reply draft (poller idempotency)",
            msg.message_id,
            identity_id,
        )
        return "skipped"

    retry_gateway_only = bool(
        isinstance(dispatch_status, dict)
        and dispatch_status.get("should_retry_gateway_only")
    )
    mismatch = mailbox_mismatch_signal(
        bridge,
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        detected_mailbox_email=mailbox_email,
    )

    event_body = {
        "identity_id": identity_id,
        "event_type": "kol_inbound_reply",
        "actor": "cron",
        "campaign_id": campaign_id,
        "env": env,
        "payload": {
            "message_id": msg.message_id,
            "thread_id": msg.thread_id,
            "in_reply_to": msg.in_reply_to,
            "from_addr": msg.from_addr,
            "to": msg.to,
            "cc": msg.cc,
            "subject": msg.subject,
            "snippet": msg.snippet,
            "body": clip_text(msg.body, 8000),
            "date": msg.date,
            "detected_mailbox_user_id": mailbox_user_id or None,
            "detected_mailbox_email": mailbox_email or None,
            "anomaly_signals": {
                "thread_integrity": {
                    "status": matched.thread_integrity,
                    "matched_by": matched.matched_by,
                    "history_thread_id": matched.history_thread_id or msg.thread_id,
                },
                "identity_integrity": {
                    "status": matched.identity_integrity,
                    "sender_email": matched.sender_email,
                    "expected_email": matched.expected_email,
                    "reasons": matched.reasons,
                },
                "content_risk": matched.content_risk,
                "risk_controls": {
                    **matched.risk_controls,
                    **({"allow_autoflow": False} if mismatch.get("mailbox_mismatch") else {}),
                },
                **mismatch,
            },
        },
    }

    if not retry_gateway_only:
        try:
            bridge.write_inbound_event(event_body)
        except BridgeRequestError as exc:
            log.error("[retry] bridge write_inbound_event failed for msg=%s: %s", msg.message_id, exc)
            return "retry"
    else:
        log.info(
            "[retry-gateway] msg=%s identity=%s inbound event exists, no draft yet",
            msg.message_id,
            identity_id,
        )

    mismatch_outcome = handle_mailbox_mismatch(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        msg=msg,
        mailbox_email=mailbox_email,
        mismatch=mismatch,
    )
    if mismatch_outcome == "skip":
        return "skipped"
    if mismatch_outcome == "retry":
        return "retry"

    session_id = f"kol-reply:{env}:{identity_id}:{msg.message_id}"
    if retry_gateway_only:
        session_id = f"{session_id}:retry-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}"

    try:
        input_text = json.dumps({
            "pending_replies": [
                pending_reply_payload(
                    bridge,
                    client=client,
                    msg=msg,
                    matched=matched,
                    env=env,
                    mailbox_user_id=mailbox_user_id,
                    mailbox_email=mailbox_email,
                )
            ],
        }, indent=2, ensure_ascii=False)
    except (GmailUnavailable, BridgeRequestError) as exc:
        log.error("[retry] pending_reply_payload failed msg=%s: %s", msg.message_id, exc)
        return "retry"

    run_id = deps.gateway.run(
        instructions=dispatcher_instructions(),
        input_text=input_text,
        session_id=session_id,
    )
    if not run_id:
        log.error(
            "[retry] gateway dispatch did not return run_id for msg=%s — inbound event"
            " written; will retry via should_retry_gateway_only",
            msg.message_id,
        )
        return "retry"

    register_console_run(
        campaign_id=campaign_id,
        env=env,
        run_id=run_id,
        session_id=session_id,
    )
    log.info(
        "dispatched msg=%s identity=%s campaign=%s run_id=%s thread=%s identity=%s risk=%s",
        msg.message_id,
        identity_id,
        campaign_id,
        run_id,
        matched.thread_integrity,
        matched.identity_integrity,
        matched.content_risk,
    )
    return "dispatched"
