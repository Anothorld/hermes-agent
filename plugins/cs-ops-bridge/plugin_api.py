"""CS Ops Bridge — HTTP API for Povison customer service."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import cal
from .classify_intent import classify_intent

log = logging.getLogger(__name__)
router = APIRouter()

_SECRETS_PATH = Path(os.path.expanduser("~/.hermes/cs-ops-bridge/secrets.yaml"))
_OPEN_MODE_WARNED = False


def _load_bridge_key() -> Optional[str]:
    env = os.environ.get("HERMES_CS_OPS_BRIDGE_KEY")
    if env:
        return env.strip() or None
    if _SECRETS_PATH.exists():
        for raw in _SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip() == "bridge_key":
                    return v.strip().strip("'\"") or None
    return None


def _require_bridge_key(provided: Optional[str]) -> None:
    expected = _load_bridge_key()
    global _OPEN_MODE_WARNED
    if expected is None:
        if not _OPEN_MODE_WARNED:
            log.warning("cs-ops-bridge: no API key configured — open mode (dev only)")
            _OPEN_MODE_WARNED = True
        return
    if provided is None or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="invalid or missing X-Bridge-Key")


class EnqueueBody(BaseModel):
    quickcep_session_id: str
    message_id: str
    chat_session_id: Optional[str] = None
    customer_email: Optional[str] = None
    env: str = "LIVE"


class EventBody(BaseModel):
    quickcep_session_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    env: str = "LIVE"


class FactsBody(BaseModel):
    quickcep_session_id: str
    namespaces: dict[str, dict[str, Any]]
    env: str = "LIVE"


class SessionStatusBody(BaseModel):
    quickcep_session_id: str
    status: str
    env: str = "LIVE"


class ClassifyBody(BaseModel):
    subject: str = ""
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EscalationOpenBody(BaseModel):
    quickcep_session_id: str
    reason: str
    urgency: str = "medium"
    question_to_operator: Optional[str] = None
    customer_email: Optional[str] = None
    email_summary: Optional[str] = None
    email_quote: Optional[str] = None
    escalation_message: Optional[str] = None
    auto_send_feishu: bool = True
    feishu_chat_id: Optional[str] = None
    feishu_thread_id: Optional[str] = None
    feishu_message_id: Optional[str] = None
    resume_context: dict[str, Any] = Field(default_factory=dict)
    env: str = "LIVE"


class EscalationResumeBody(BaseModel):
    operator_answer: str
    decided_by: str = "console_operator"
    env: str = "LIVE"


class SessionRelaunchBody(BaseModel):
    env: str = "LIVE"
    message_id: Optional[str] = None


class EscalationResolveBody(BaseModel):
    decision: str
    decided_by: str
    operator_answer: Optional[str] = None
    final_state: str = "resolved"


class HandoffBody(BaseModel):
    phase: str
    env: str = "LIVE"
    customer_need: str = ""
    actions_taken: str = ""
    follow_up: str = ""
    operator_hint: str = ""
    error: str = ""
    urgency: str = "medium"
    feishu_thread_id: Optional[str] = None
    classify: dict[str, Any] = Field(default_factory=dict)
    chat_session_id: Optional[str] = None
    skip_quickcep: bool = False

    @field_validator("phase")
    @classmethod
    def _validate_handoff_phase(cls, value: str) -> str:
        from .session_handoff import HANDOFF_PHASES, normalize_handoff_phase

        phase = normalize_handoff_phase(value)
        if phase not in HANDOFF_PHASES:
            allowed = ", ".join(sorted(HANDOFF_PHASES))
            raise ValueError(f"invalid handoff phase {value!r}; allowed: {allowed}")
        return phase


@router.get("/health")
def health() -> dict[str, Any]:
    return cal.health()


@router.get("/feishu-probe")
def feishu_probe() -> dict[str, Any]:
    """Report configured Feishu bot identity and whether it can post to the escalation chat."""
    from .feishu_client import (
        escalation_chat_id,
        feishu_credentials_present,
        send_group_text,
        tenant_access_token,
    )
    import json
    import urllib.error
    import urllib.request

    chat_id = escalation_chat_id()
    out: dict[str, Any] = {
        "credentials_present": feishu_credentials_present(),
        "escalation_chat_id": chat_id,
        "app_id": (os.environ.get("FEISHU_APP_ID") or "")[:8] + "…" if os.environ.get("FEISHU_APP_ID") else None,
    }
    token = tenant_access_token()
    if not token:
        out["bot"] = None
        out["send_test"] = {"ok": False, "error": "missing or invalid FEISHU credentials"}
        return out
    try:
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/bot/v3/info/",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            bot_payload = json.loads(resp.read().decode())
        bot = bot_payload.get("bot") or {}
        out["bot"] = {"app_name": bot.get("app_name"), "activate_status": bot.get("activate_status")}
    except urllib.error.HTTPError as exc:
        out["bot"] = {"error": f"HTTP {exc.code}"}
    if chat_id:
        probe = send_group_text(chat_id=chat_id, text="[cs-ops-bridge feishu-probe] safe to ignore", token=token)
        out["send_test"] = {
            "ok": probe.ok,
            "message_id": probe.message_id,
            "error": probe.error,
        }
    return out


@router.get("/admin/perf-snapshot")
def perf_snapshot(env: str = Query("LIVE")) -> dict[str, Any]:
    return cal.perf_snapshot(env=env)


@router.post("/sessions/enqueue")
def enqueue_session(
    body: EnqueueBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    return cal.enqueue_session(
        quickcep_session_id=body.quickcep_session_id,
        chat_session_id=body.chat_session_id,
        customer_email=body.customer_email,
        message_id=body.message_id,
        env=body.env,
    )


@router.get("/sessions")
def list_sessions_route(
    env: str = Query("LIVE"),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    return {"sessions": cal.list_sessions(env=env, status=status, q=q, limit=limit)}


@router.post("/sessions/{quickcep_session_id}/handoff")
def apply_session_handoff(
    quickcep_session_id: str,
    body: HandoffBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from .session_handoff import apply_handoff

    context = {
        "customer_need": body.customer_need,
        "actions_taken": body.actions_taken,
        "follow_up": body.follow_up,
        "operator_hint": body.operator_hint,
        "error": body.error,
        "urgency": body.urgency,
        "feishu_thread_id": body.feishu_thread_id,
        "classify": body.classify,
    }
    try:
        result = apply_handoff(
            quickcep_session_id=quickcep_session_id,
            phase=body.phase,
            env=body.env,
            context=context,
            chat_session_id=body.chat_session_id,
            skip_quickcep=body.skip_quickcep,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok") and result.get("error") == "session not found":
        raise HTTPException(status_code=404, detail="session not found")
    return result


@router.get("/sessions/{quickcep_session_id}/dispatch-context")
def get_dispatch_context(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
) -> dict[str, Any]:
    from .bridge_agent_contract import agent_tool_paths

    ctx = cal.get_dispatch_context(quickcep_session_id=quickcep_session_id, env=env)
    if not ctx:
        raise HTTPException(status_code=404, detail="session not found")
    ctx["agent_tool_paths"] = agent_tool_paths()
    return ctx


@router.post("/sessions/status")
def update_session_status(
    body: SessionStatusBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    sess = cal.get_session(quickcep_session_id=body.quickcep_session_id, env=body.env)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    cal.update_session_status(session_row_id=sess["id"], status=body.status)
    return {"ok": True}


@router.post("/events")
def write_event(
    body: EventBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    ok = cal.write_event(
        quickcep_session_id=body.quickcep_session_id,
        event_type=body.event_type,
        payload=body.payload,
        env=body.env,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


@router.post("/facts")
def write_facts(
    body: FactsBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    ok = cal.write_facts(
        quickcep_session_id=body.quickcep_session_id,
        namespaces=body.namespaces,
        env=body.env,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


@router.post("/logic/classify-intent")
def classify_intent_route(body: ClassifyBody) -> dict[str, Any]:
    result = classify_intent(subject=body.subject, body=body.body, metadata=body.metadata)
    from .session_handoff import inquiry_tag_for_category

    tag_id = inquiry_tag_for_category(result.get("category"))
    if tag_id:
        result = {**result, "inquiry_tag_id": tag_id}
    return result


@router.get("/escalations")
def list_escalations(
    state: Optional[str] = Query(None),
    env: str = Query("LIVE"),
) -> dict[str, Any]:
    return {"escalations": cal.list_escalations(state=state, env=env)}


@router.get("/escalations/{escalation_id}")
def get_escalation(escalation_id: int) -> dict[str, Any]:
    row = cal.get_escalation(escalation_id=escalation_id)
    if not row:
        raise HTTPException(status_code=404, detail="escalation not found")
    return row


@router.post("/escalations")
def open_escalation(
    body: EscalationOpenBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    eid = cal.open_escalation(
        quickcep_session_id=body.quickcep_session_id,
        reason=body.reason,
        urgency=body.urgency,
        question_to_operator=body.question_to_operator,
        feishu_chat_id=body.feishu_chat_id,
        feishu_thread_id=body.feishu_thread_id,
        feishu_message_id=body.feishu_message_id,
        resume_context=body.resume_context,
        env=body.env,
    )
    if eid is None:
        raise HTTPException(status_code=404, detail="session not found")

    feishu_result: dict[str, Any] = {"skipped": True}
    should_send = body.auto_send_feishu and not (body.feishu_message_id and body.feishu_thread_id)
    if should_send:
        from . import feishu_notify

        try:
            feishu_notify.validate_feishu_notify_inputs(
                auto_send_feishu=True,
                escalation_message=body.escalation_message,
                customer_email=body.customer_email,
                email_summary=body.email_summary,
                email_quote=body.email_quote,
                quickcep_session_id=body.quickcep_session_id,
                env=body.env,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        send = feishu_notify.notify_escalation_opened(
            escalation_id=eid,
            quickcep_session_id=body.quickcep_session_id,
            reason=body.reason,
            urgency=body.urgency,
            question_to_operator=body.question_to_operator,
            customer_email=body.customer_email,
            email_summary=body.email_summary,
            email_quote=body.email_quote,
            escalation_message=body.escalation_message,
            feishu_chat_id=body.feishu_chat_id,
            env=body.env,
            auto_send_feishu=True,
        )
        feishu_result = {
            "ok": send.ok,
            "message_id": send.message_id,
            "thread_id": send.thread_id,
            "chat_id": send.chat_id,
            "error": send.error,
        }
        if send.ok:
            cal.update_escalation_feishu(
                escalation_id=eid,
                feishu_chat_id=send.chat_id,
                feishu_thread_id=send.thread_id,
                feishu_message_id=send.message_id,
            )

    return {"escalation_id": eid, "feishu": feishu_result}


@router.patch("/escalations/{escalation_id}")
def resolve_escalation(
    escalation_id: int,
    body: EscalationResolveBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from .escalation_resolve import resolve_escalation_operational

    result = resolve_escalation_operational(
        escalation_id=escalation_id,
        decision=body.decision,
        decided_by=body.decided_by,
        operator_answer=body.operator_answer,
        final_state=body.final_state,
    )
    if not result.get("ok"):
        detail = result.get("error") or "resolve failed"
        status = 404 if detail == "escalation not found" else 409
        raise HTTPException(status_code=status, detail=detail)
    return result


@router.post("/escalations/{escalation_id}/resume")
def resume_escalation_route(
    escalation_id: int,
    body: EscalationResumeBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from .escalation_resume import resume_escalation

    result = resume_escalation(
        escalation_id=escalation_id,
        operator_answer=body.operator_answer,
        decided_by=body.decided_by,
        env=body.env,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "resume failed")
    return result


@router.post("/sessions/{quickcep_session_id}/relaunch")
def relaunch_session_route(
    quickcep_session_id: str,
    body: SessionRelaunchBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Re-trigger gateway process run for failed or stuck sessions."""
    _require_bridge_key(x_bridge_key)
    from .email_channel import session_is_email
    from .gateway_client import GatewayClient

    if not session_is_email(quickcep_session_id):
        raise HTTPException(status_code=409, detail="non_email_channel")

    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=body.env)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    if sess["status"] == "awaiting_expert":
        raise HTTPException(status_code=409, detail=f"session busy: {sess['status']}")
    msg_id = body.message_id or sess.get("last_message_id") or "manual-relaunch"
    cal.update_session_status(session_row_id=sess["id"], status="processing")
    outcome = GatewayClient.from_env().start_process_run(
        quickcep_session_id=quickcep_session_id,
        env=body.env,
        message_id=str(msg_id),
        brief_extra="source: console_relaunch",
    )
    if not outcome.run_id:
        cal.update_session_status(session_row_id=sess["id"], status="failed")
        raise HTTPException(status_code=502, detail="gateway launch failed")
    return {"ok": True, "run_id": outcome.run_id}


@router.get("/watcher/status")
def watcher_status() -> dict[str, Any]:
    return {
        "quickcep": cal.get_poller_state("quickcep_watcher"),
        "feishu": cal.get_poller_state("feishu_escalation_poller"),
        "escalation_timeout": cal.get_poller_state("escalation_timeout"),
        "processing_stale": cal.get_poller_state("processing_stale"),
    }


@router.post("/watcher/run-once")
async def watcher_run_once(
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from . import quickcep_watcher, feishu_escalation_poller, processing_stale

    rest = quickcep_watcher.run_rest_reconcile_once()
    feishu = feishu_escalation_poller.poll_once()
    stale = processing_stale.check_processing_stale_once()
    return {"rest_reconcile": rest, "feishu_poll": feishu, "processing_stale": stale}
