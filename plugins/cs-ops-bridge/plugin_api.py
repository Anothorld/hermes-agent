"""CS Ops Bridge — HTTP API for Povison customer service."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query
from pydantic import BaseModel, Field

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
    feishu_chat_id: Optional[str] = None
    feishu_thread_id: Optional[str] = None
    feishu_message_id: Optional[str] = None
    resume_context: dict[str, Any] = Field(default_factory=dict)
    env: str = "LIVE"


class EscalationResolveBody(BaseModel):
    decision: str
    decided_by: str
    operator_answer: Optional[str] = None
    final_state: str = "resolved"


@router.get("/health")
def health() -> dict[str, Any]:
    return cal.health()


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


@router.get("/sessions/{quickcep_session_id}/dispatch-context")
def get_dispatch_context(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
) -> dict[str, Any]:
    ctx = cal.get_dispatch_context(quickcep_session_id=quickcep_session_id, env=env)
    if not ctx:
        raise HTTPException(status_code=404, detail="session not found")
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
    return classify_intent(subject=body.subject, body=body.body, metadata=body.metadata)


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
    return {"escalation_id": eid}


@router.patch("/escalations/{escalation_id}")
def resolve_escalation(
    escalation_id: int,
    body: EscalationResolveBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    ok = cal.resolve_escalation(
        escalation_id=escalation_id,
        decision=body.decision,
        decided_by=body.decided_by,
        operator_answer=body.operator_answer,
        final_state=body.final_state,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="escalation not found")
    return {"ok": True}


@router.get("/watcher/status")
def watcher_status() -> dict[str, Any]:
    return {
        "quickcep": cal.get_poller_state("quickcep_watcher"),
        "feishu": cal.get_poller_state("feishu_escalation_poller"),
        "escalation_timeout": cal.get_poller_state("escalation_timeout"),
    }


@router.post("/watcher/run-once")
async def watcher_run_once(
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from . import quickcep_watcher, feishu_escalation_poller

    rest = quickcep_watcher.run_rest_reconcile_once()
    feishu = feishu_escalation_poller.poll_once()
    return {"rest_reconcile": rest, "feishu_poll": feishu}
