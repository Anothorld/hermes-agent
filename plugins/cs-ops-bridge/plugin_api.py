"""CS Ops Bridge — HTTP API for Povison customer service."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from . import cal
from .bridge_secrets import load_bridge_key
from .classify_intent import classify_intent

log = logging.getLogger(__name__)
router = APIRouter()

_OPEN_MODE_WARNED = False


def _load_bridge_key() -> Optional[str]:
    return load_bridge_key()


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


class ConsoleEscalationReplyBody(BaseModel):
    operator_answer: str
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None
    env: str = "LIVE"


class AutopilotSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    send_after_sec: Optional[int] = None
    updated_by: str = "console"


class SessionRelaunchBody(BaseModel):
    env: str = "LIVE"
    message_id: Optional[str] = None


class RunFinishedBody(BaseModel):
    session_id: str
    completed: bool = True
    interrupted: bool = False
    env: str = "LIVE"


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


class DraftBody(BaseModel):
    env: str = "LIVE"
    draft_html: str
    attachments: list[Any] = Field(default_factory=list)
    source: str = "agent"
    subject: Optional[str] = None
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        if value not in ("agent", "operator_edit", "resume_agent"):
            raise ValueError(f"invalid draft source {value!r}; allowed: agent, operator_edit, resume_agent")
        return value


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
    offset: int = Query(0, ge=0),
    since: Optional[str] = Query(None, description="ISO lower bound on first-active time (inclusive)"),
    until: Optional[str] = Query(None, description="ISO upper bound on first-active time (exclusive)"),
    with_counts: bool = Query(False),
    response: Response = None,
) -> dict[str, Any]:
    sessions = cal.list_sessions(
        env=env, status=status, q=q, limit=limit, offset=offset, since=since, until=until,
    )
    total = cal.count_sessions(env=env, status=status, q=q, since=since, until=until)
    out: dict[str, Any] = {
        "sessions": sessions,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(sessions) < total,
    }
    if with_counts:
        out["counts"] = cal.session_counts(env=env)
    if response is not None:
        response.headers["Cache-Control"] = "private, max-age=3"
    return out


@router.get("/daily-report/stats")
def daily_report_stats_route(
    env: str = Query("LIVE"),
    since: str = Query(..., description="ISO lower bound (inclusive), e.g. 2026-07-01T00:00:00.000Z"),
    until: str = Query(..., description="ISO upper bound (exclusive)"),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """One-shot aggregate for the daily report (PR-daily-fix).

    Returns processed sessions in the window (server-side date filter on
    first-active time, full limit — not the legacy 50-row page), escalations
    counted by ``created_at`` (not snapshot status), and the set of session
    ids that fired a ``draft_saved`` event (fallback for the draft_source
    tracking bug). Single call → consistent snapshot.
    """
    return cal.daily_report_stats(env=env, since=since, until=until, limit=limit)


@router.get("/sessions/{quickcep_session_id}/workbench")
def get_session_workbench(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    response: Response = None,
) -> dict[str, Any]:
    """L1 pure-CAL aggregate for the Console workbench (PR1.4). Zero QuickCEP calls."""
    result = cal.get_workbench(quickcep_session_id=quickcep_session_id, env=env)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    if response is not None:
        # Contains operator drafts (per-user); private, short browser cache.
        response.headers["Cache-Control"] = "private, max-age=2"
    return result


@router.get("/sessions/{quickcep_session_id}/state")
def get_session_state_route(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    response: Response = None,
) -> dict[str, Any]:
    """L3 lightweight poll payload (PR1.4)."""
    result = cal.get_session_state(quickcep_session_id=quickcep_session_id, env=env)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    if response is not None:
        # Pure CAL, no per-user data → public 2s cache.
        response.headers["Cache-Control"] = "public, max-age=2"
    return result


@router.get("/sessions/{quickcep_session_id}/messages")
def get_session_messages(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    since: Optional[str] = Query(None, description="return only messages after this message id"),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
    response: Response = None,
) -> dict[str, Any]:
    """L2 live message history (PR1.5). Incremental via ``since``."""
    _require_bridge_key(x_bridge_key)
    if not cal.get_session(quickcep_session_id=quickcep_session_id, env=env):
        raise HTTPException(status_code=404, detail="session not found")
    from .quickcep_live import fetch_messages

    if response is not None:
        # Backend has a 15s cache; allow a 5s browser cache with SWR to mask
        # repeated clicks without going stale beyond the backend TTL.
        response.headers["Cache-Control"] = "private, max-age=5, stale-while-revalidate=10"
    return fetch_messages(quickcep_session_id=quickcep_session_id, since=since)


@router.get("/sessions/{quickcep_session_id}/tags")
def get_session_tags(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
    response: Response = None,
) -> dict[str, Any]:
    """L2 session tags reverse-resolved to names, 300s cache (PR1.5)."""
    _require_bridge_key(x_bridge_key)
    if not cal.get_session(quickcep_session_id=quickcep_session_id, env=env):
        raise HTTPException(status_code=404, detail="session not found")
    from .quickcep_live import fetch_session_tags

    if response is not None:
        # Align with the 300s backend cache; 30s fresh + 270s SWR.
        response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=270"
    return fetch_session_tags(quickcep_session_id=quickcep_session_id)


@router.get("/sessions/{quickcep_session_id}/orders")
def get_session_orders(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
    response: Response = None,
) -> dict[str, Any]:
    """L2 customer orders + intention_tags, 60s cache (PR1.5)."""
    _require_bridge_key(x_bridge_key)
    if not cal.get_session(quickcep_session_id=quickcep_session_id, env=env):
        raise HTTPException(status_code=404, detail="session not found")
    from .quickcep_live import fetch_session_orders

    if response is not None:
        # Align with the 60s backend cache; 30s fresh + 30s SWR.
        response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=30"
    return fetch_session_orders(quickcep_session_id=quickcep_session_id, env=env)


class NoteBody(BaseModel):
    env: str = "LIVE"
    chat_session_id: Optional[str] = None
    text: str
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None


class SendReplyBody(BaseModel):
    env: str = "LIVE"
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None
    subject: Optional[str] = None


class CloseSessionBody(BaseModel):
    env: str = "LIVE"
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None
    note: str = ""
    mark_reviewed: bool = True


@router.post("/sessions/{quickcep_session_id}/send-reply")
def send_session_reply(
    quickcep_session_id: str,
    body: SendReplyBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Send the CAL-stored draft to the customer (PR1.6).

    Service-initiated send (sanctioned guard bypass via scoped subprocess env).
    Reads draft from CAL, runs guards, calls quickcep_cli send-email, backfills
    the outbound message_id, and applies operator_sent handoff.
    """
    _require_bridge_key(x_bridge_key)
    from .send_reply import send_reply

    result = send_reply(
        quickcep_session_id=quickcep_session_id,
        env=body.env,
        operator_id=body.operator_id,
        operator_name=body.operator_name,
        subject_override=body.subject,
    )
    if result.get("error") == "session not found":
        raise HTTPException(status_code=404, detail="session not found")
    if result.get("error") == "no_draft":
        raise HTTPException(status_code=409, detail=result.get("error_detail", "no draft"))
    if result.get("error") == "guard_blocked":
        raise HTTPException(status_code=422, detail=result.get("error_detail"))
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@router.post("/sessions/{quickcep_session_id}/close")
def close_session_route(
    quickcep_session_id: str,
    body: CloseSessionBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """End the QuickCEP chat session (chat_end) and optionally mark CAL reviewed."""
    _require_bridge_key(x_bridge_key)
    from .close_session import close_session

    result = close_session(
        quickcep_session_id=quickcep_session_id,
        env=body.env,
        operator_id=body.operator_id,
        operator_name=body.operator_name,
        mark_reviewed=body.mark_reviewed,
        note=body.note,
    )
    if result.get("error") == "quickcep_cli_not_found":
        raise HTTPException(status_code=500, detail=result)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@router.post("/sessions/{quickcep_session_id}/attachments/upload")
async def upload_session_attachment(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    file: UploadFile = File(...),
    feature: str = Form("email"),
    operator_id: Optional[str] = Form(None),
    operator_name: Optional[str] = Form(None),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Upload an attachment to QuickCEP CDN (PR1.7).

    Multipart upload → saved to a session-scoped temp path →
    quickcep_cdn.upload_file_to_cdn → returns the attachment object
    ({fileName, fileSize, url}) the FE attaches to the draft.
    """
    _require_bridge_key(x_bridge_key)
    if not cal.get_session(quickcep_session_id=quickcep_session_id, env=env):
        raise HTTPException(status_code=404, detail="session not found")
    import tempfile
    import uuid

    from .quickcep_cdn import upload_file_to_cdn

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    original = file.filename or "upload.bin"
    suffix = Path(original).suffix
    with tempfile.NamedTemporaryFile(
        delete=False, prefix=f"attach-{quickcep_session_id}-{uuid.uuid4().hex[:8]}-",
        suffix=suffix,
    ) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        result = upload_file_to_cdn(tmp_path, feature=feature)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "upload failed")
    cal.write_event(
        quickcep_session_id=quickcep_session_id,
        env=env,
        event_type="attachment_uploaded",
        payload={
            "fileName": result.get("fileName"),
            "fileSize": result.get("fileSize"),
            "url": result.get("url"),
            "operator_id": operator_id,
            "operator_name": operator_name,
        },
    )
    return result


@router.post("/sessions/{quickcep_session_id}/note")
def add_session_note(
    quickcep_session_id: str,
    body: NoteBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Add a QuickCEP internal note (PR1.5). Reuses session_handoff.apply_quickcep_note."""
    _require_bridge_key(x_bridge_key)
    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=body.env)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    chat_session_id = body.chat_session_id or sess.get("chat_session_id") or ""
    if not chat_session_id:
        raise HTTPException(status_code=400, detail="chat_session_id required (not on session row)")
    from .quickcep_live import add_note, invalidate_cache

    result = add_note(
        quickcep_session_id=quickcep_session_id,
        chat_session_id=chat_session_id,
        text=body.text,
    )
    # Record an audit event + drop caches (note add bumps session activity).
    cal.write_event(
        quickcep_session_id=quickcep_session_id,
        env=body.env,
        event_type="operator_note_added",
        payload={"text": body.text, "operator_id": body.operator_id,
                 "operator_name": body.operator_name, "ok": result.get("ok")},
    )
    invalidate_cache(quickcep_session_id)
    return result


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


@router.put("/sessions/{quickcep_session_id}/draft")
def save_session_draft(
    quickcep_session_id: str,
    body: DraftBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Persist a reply draft to CAL (PR1.3 / §4.13).

    Replaces writing drafts to QuickCEP. The agent (via cs_bridge_tool draft-save)
    and the Console composer both write through this endpoint. Autopilot lock
    enforcement (PR2) is applied via cal.save_draft's lock_check hook.
    """
    _require_bridge_key(x_bridge_key)
    from .autopilot import autopilot_lock_check
    from .draft_guard import guard_draft_content

    # Server-side guard for Console-originated drafts (agent path guards in
    # cs_bridge_tool; both must enforce the same policy — PR1.9).
    block = guard_draft_content(body.draft_html, body.attachments)
    if block:
        raise HTTPException(status_code=422, detail=block)

    result = cal.save_draft(
        quickcep_session_id=quickcep_session_id,
        draft_html=body.draft_html,
        attachments=body.attachments,
        source=body.source,
        subject=body.subject,
        env=body.env,
        operator_id=body.operator_id,
        operator_name=body.operator_name,
        lock_check=autopilot_lock_check,
    )
    if not result.get("success") and result.get("error") == "session not found":
        raise HTTPException(status_code=404, detail="session not found")
    if not result.get("success") and result.get("error") == "draft_locked_autopilot":
        raise HTTPException(status_code=409, detail=result.get("error_detail", "draft locked by autopilot"))
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


@router.get("/sessions/{quickcep_session_id}/attachment-guard-context")
def get_attachment_guard_context(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
) -> dict[str, Any]:
    """Read-only context for draft-save PDF attachment guard (resuming escalation allow list)."""
    esc = cal.get_resuming_escalation_for_session(
        quickcep_session_id=quickcep_session_id,
        env=env,
    )
    if not esc:
        return {
            "quickcep_session_id": quickcep_session_id,
            "env": env,
            "escalation_id": None,
            "allowed_attachment_urls": [],
        }
    ctx = esc.get("resume_context") or {}
    return {
        "quickcep_session_id": quickcep_session_id,
        "env": env,
        "escalation_id": esc.get("id"),
        "allowed_attachment_urls": list(ctx.get("allowed_attachment_urls") or []),
    }


@router.post("/sessions/status")
def update_session_status(
    body: SessionStatusBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    sess = cal.get_session(quickcep_session_id=body.quickcep_session_id, env=body.env)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    # Bridge guard (§4.13 B): refuse direct draft_ready status set without a CAL
    # draft (agent contract step 10 fallback must not bypass the draft-save guard).
    if body.status == "draft_ready":
        from .session_handoff import _legacy_draft_mode
        if not _legacy_draft_mode() and not (sess.get("draft_html") or "").strip():
            raise HTTPException(
                status_code=409,
                detail="draft_ready_requires_cal_draft: 先 cs_bridge_tool draft-save 保存草稿到 CAL，再设置 draft_ready。",
            )
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


@router.get("/escalations/{escalation_id}/upload-link")
def get_escalation_upload_link(
    escalation_id: int,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Return signed vault upload URL (ops backfill when Feishu post omitted the link)."""
    _require_bridge_key(x_bridge_key)
    row = cal.get_escalation(escalation_id=escalation_id)
    if not row:
        raise HTTPException(status_code=404, detail="escalation not found")
    from .escalation_attachment_vault import build_public_upload_url

    try:
        url = build_public_upload_url(escalation_id=escalation_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"escalation_id": escalation_id, "upload_url": url, "state": row.get("state")}


@router.post("/escalations/{escalation_id}/feishu-upload-link")
def post_feishu_upload_link(
    escalation_id: int,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Reply on the Feishu escalation thread with the vault upload link."""
    _require_bridge_key(x_bridge_key)
    from . import feishu_notify

    row = cal.get_escalation(escalation_id=escalation_id)
    if not row:
        raise HTTPException(status_code=404, detail="escalation not found")
    root = str(row.get("feishu_message_id") or row.get("feishu_thread_id") or "")
    if not root:
        raise HTTPException(status_code=409, detail="escalation has no feishu root message")
    send = feishu_notify.notify_vault_upload_link(
        escalation_id=escalation_id,
        feishu_root_message_id=root,
    )
    if not send.ok:
        raise HTTPException(status_code=502, detail=send.error or "feishu reply failed")
    return {"ok": True, "escalation_id": escalation_id, "feishu_message_id": send.message_id}


@router.get("/escalations/{escalation_id}/upload", response_class=HTMLResponse)
def vault_upload_page(
    escalation_id: int,
    token: str = Query(..., description="Signed upload token from Feishu escalation message"),
) -> HTMLResponse:
    from .escalation_attachment_vault import upload_page_html, verify_upload_token

    if not verify_upload_token(escalation_id=escalation_id, token=token):
        raise HTTPException(status_code=403, detail="invalid or expired upload token")
    return HTMLResponse(upload_page_html(escalation_id=escalation_id, token=token))


@router.post("/escalations/{escalation_id}/vault")
async def vault_upload_file(
    escalation_id: int,
    token: str = Query(...),
    file: UploadFile = File(...),
    uploaded_by: Optional[str] = Form(None),
) -> dict[str, Any]:
    from .escalation_attachment_vault import store_upload, verify_upload_token

    if not verify_upload_token(escalation_id=escalation_id, token=token):
        raise HTTPException(status_code=403, detail="invalid or expired upload token")
    data = await file.read()
    result = store_upload(
        escalation_id=escalation_id,
        file_bytes=data,
        original_name=file.filename or "upload.bin",
        content_type=file.content_type,
        uploaded_by=uploaded_by,
    )
    if not result.get("ok"):
        status = int(result.get("status") or 422)
        raise HTTPException(status_code=status, detail=result.get("error") or "upload failed")
    return result


def _auth_vault(x_bridge_key: Optional[str], token: Optional[str], escalation_id: int) -> None:
    """Allow either a valid bridge key (console/ops) or a signed upload token
    (public Feishu upload page scoped to this escalation)."""
    from .escalation_attachment_vault import verify_upload_token

    if token and verify_upload_token(escalation_id=escalation_id, token=token):
        return
    _require_bridge_key(x_bridge_key)


@router.get("/escalations/{escalation_id}/vault")
def vault_list_files(
    escalation_id: int,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
    token: Optional[str] = Query(None, description="Signed upload token (public page)"),
) -> dict[str, Any]:
    _auth_vault(x_bridge_key, token, escalation_id)
    from .escalation_attachment_vault import list_vault_files

    return {"escalation_id": escalation_id, "files": list_vault_files(escalation_id=escalation_id)}


@router.delete("/escalations/{escalation_id}/vault/{link_id}")
def vault_delete_file(
    escalation_id: int,
    link_id: str,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
    token: Optional[str] = Query(None, description="Signed upload token (public page)"),
) -> dict[str, Any]:
    _auth_vault(x_bridge_key, token, escalation_id)
    links = cal.list_vault_links_for_escalation(escalation_id=escalation_id)
    if not any(str(l.get("id")) == link_id for l in links):
        raise HTTPException(status_code=404, detail="vault link not found")
    ok = cal.delete_vault_link(link_id=link_id)
    return {"ok": ok, "link_id": link_id}


@router.get("/escalations/{escalation_id}/vault/{link_id}/content")
def vault_file_content(
    escalation_id: int,
    link_id: str,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
    token: Optional[str] = Query(None, description="Signed upload token (public page)"),
):
    """Stream the raw bytes of a vault file for inline preview/download."""
    _auth_vault(x_bridge_key, token, escalation_id)
    from fastapi.responses import Response

    from .escalation_attachment_vault import resolve_blob_bytes

    links = cal.list_vault_links_for_escalation(escalation_id=escalation_id)
    link = next((l for l in links if str(l.get("id")) == link_id), None)
    if not link:
        raise HTTPException(status_code=404, detail="vault link not found")
    blob_md5 = link.get("blob_md5") or ""
    data = resolve_blob_bytes(blob_md5=blob_md5)
    if data is None:
        raise HTTPException(status_code=404, detail="blob not found")
    content_type = link.get("content_type") or "application/octet-stream"
    name = link.get("original_name") or "file"
    inline_types = ("image/", "application/pdf", "text/")
    disposition = "inline" if any(content_type.startswith(t) for t in inline_types) else "attachment"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'{disposition}; filename="{name}"'},
    )


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


@router.post("/escalations/{escalation_id}/console-reply")
def console_escalation_reply_route(
    escalation_id: int,
    body: ConsoleEscalationReplyBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Console-originated escalation reply (PR1.8).

    Atomic first-wins claim (awaiting_answer → resuming), [ESC-LOCK] Feishu
    notice, gateway resume run, audit event. Loses the first-wins race with a
    409 ``already_claimed``.
    """
    _require_bridge_key(x_bridge_key)
    from .escalation_resume import console_reply_escalation

    result = console_reply_escalation(
        escalation_id=escalation_id,
        operator_answer=body.operator_answer,
        operator_id=body.operator_id,
        operator_name=body.operator_name,
        env=body.env,
    )
    if result.get("error") == "escalation not found":
        raise HTTPException(status_code=404, detail="escalation not found")
    if result.get("error") == "already_claimed":
        raise HTTPException(status_code=409, detail=result.get("error_detail", "already claimed"))
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@router.post("/sessions/{quickcep_session_id}/relaunch")
def relaunch_session_route(
    quickcep_session_id: str,
    body: SessionRelaunchBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Re-trigger gateway processing for failed or stuck sessions.

    Auto-routes: if the session has an escalation with a recorded expert answer
    (resume failure), the call is redirected to escalation resume retry instead
    of a fresh inbound run. Falls through to the normal inbound relaunch when no
    such escalation exists.
    """
    _require_bridge_key(x_bridge_key)
    from .email_channel import session_is_email
    from .gateway_client import GatewayClient

    if not session_is_email(quickcep_session_id):
        raise HTTPException(status_code=409, detail="non_email_channel")

    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=body.env)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    # Resume retry routing — MUST come before the awaiting_expert 409 check:
    # ESC36/37 failures leave the session in `awaiting_expert`, and the 409
    # would block the operator from reaching the retry path. The function's
    # internal guard ensures sessions without an expert answer still fall
    # through to the 409 below.
    from .escalation_resume import retry_resume_for_session

    retry = retry_resume_for_session(quickcep_session_id=quickcep_session_id, env=body.env)
    if retry.get("kind") == "resume_retry":
        if not retry.get("ok"):
            raise HTTPException(
                status_code=502,
                detail=retry.get("error") or "resume retry failed",
            )
        return {
            "ok": True,
            "run_id": retry.get("run_id"),
            "kind": "resume_retry",
            "escalation_id": retry.get("escalation_id"),
        }

    # No resume escalation with expert answer — fall through to inbound relaunch.
    if sess["status"] == "awaiting_expert":
        raise HTTPException(status_code=409, detail=f"session busy: {sess['status']}")
    msg_id = body.message_id or sess.get("last_message_id") or "manual-relaunch"
    cal.update_session_status(session_row_id=sess["id"], status="processing")
    # ── Relaunch joinChat (fail-soft) ────────────────────────────────
    # Same fail-soft join as the inbound watcher launch path: make the AI
    # account visible in QuickCEP as soon as the session re-enters processing.
    # Failure is non-fatal — Console send-email still joins as a fallback.
    from .quickcep_join import (
        join_chat_on_launch_enabled,
        join_chat_session,
        launch_join_max_attempts,
        record_join_chat_event,
    )

    if join_chat_on_launch_enabled():
        try:
            join_result = join_chat_session(
                quickcep_session_id,
                max_attempts=launch_join_max_attempts(),
                raise_on_failure=False,
                source="relaunch",
            )
            record_join_chat_event(
                quickcep_session_id=quickcep_session_id,
                join_result=join_result,
                message_id=str(msg_id),
                env=body.env,
            )
        except Exception as exc:
            log.warning("relaunch joinChat error session=%s: %s", quickcep_session_id, exc)
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


@router.post("/internal/run-finished")
def run_finished_route(
    body: RunFinishedBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Internal endpoint called by the cs-bridge-agent-guard ``on_session_end`` hook.

    The gateway plugin fires a fire-and-forget HTTP POST here when any CS agent
    run ends. The bridge checks whether a resuming escalation is still stuck
    (handoff not applied) and notifies the operator if so. This is the detection
    path for the ESC36/37 failure mode where the agent produced gibberish and
    treated it as completion without calling ``apply-handoff``.
    """
    _require_bridge_key(x_bridge_key)
    from .escalation_resume import handle_resume_run_finished

    return handle_resume_run_finished(
        session_id=body.session_id,
        completed=body.completed,
        env=body.env,
    )


# ── Autopilot (PR2) ────────────────────────────────────────────────────


@router.get("/autopilot/settings")
def get_autopilot_settings(
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from .autopilot import get_settings

    return get_settings()


@router.put("/autopilot/settings")
def update_autopilot_settings(
    body: AutopilotSettingsBody,
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    from .autopilot import update_settings

    return update_settings(
        enabled=body.enabled,
        send_after_sec=body.send_after_sec,
        updated_by=body.updated_by,
    )


@router.get("/sessions/{quickcep_session_id}/autopilot")
def get_session_autopilot_route(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    if not cal.get_session(quickcep_session_id=quickcep_session_id, env=env):
        raise HTTPException(status_code=404, detail="session not found")
    from .autopilot import get_session_autopilot

    job = get_session_autopilot(quickcep_session_id=quickcep_session_id, env=env)
    return {"autopilot": job}


@router.post("/sessions/{quickcep_session_id}/autopilot/cancel")
def cancel_session_autopilot_route(
    quickcep_session_id: str,
    env: str = Query("LIVE"),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    if not cal.get_session(quickcep_session_id=quickcep_session_id, env=env):
        raise HTTPException(status_code=404, detail="session not found")
    from .autopilot import cancel_session_autopilot

    result = cancel_session_autopilot(quickcep_session_id=quickcep_session_id, env=env)
    if result.get("error") == "no_autopilot_job":
        raise HTTPException(status_code=404, detail="no autopilot job for this session")
    return result


@router.post("/autopilot/tick")
def autopilot_tick_route(
    env: str = Query("LIVE"),
    x_bridge_key: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Run one autopilot worker tick (claim + send due jobs). For cron/manual trigger."""
    _require_bridge_key(x_bridge_key)
    from .autopilot import run_autopilot_tick

    return run_autopilot_tick(env=env)


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
