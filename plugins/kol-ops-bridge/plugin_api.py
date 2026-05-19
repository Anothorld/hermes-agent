"""KOL Ops Bridge — HTTP API.

Mounted at ``/api/plugins/kol-ops-bridge/`` by the Hermes dashboard
plugin system.

Three audiences:

1. **External KOL Ops Console (Web backend)** — calls the read endpoints
   to render product/KOL/timeline views, and the human-in-the-loop
   write endpoints (mark contract signed, fill tracking, content
   verdict, manual alias) to advance stub stages. Authenticates with
   the bridge API key (header ``X-Bridge-Key``) on top of the dashboard
   session token.
2. **Hermes dashboard** — same code path, session-token auth only
   (no bridge key needed for read-only diagnostic views).
3. **Other Hermes skills** — import the helpers in ``cal.py`` directly
   rather than going through HTTP.

Auth model
----------
- All routes require the dashboard session token via the standard
  ``/api/plugins/...`` middleware (handled by the dashboard host).
- Routes that mutate stub stages or trigger Gateway runs additionally
  require a bridge API key (``X-Bridge-Key`` header). The key is
  read from ``HERMES_KOL_OPS_BRIDGE_KEY`` env var, falling back to
  ``~/.hermes/kol-ops-bridge/secrets.yaml``. If neither is set, the
  bridge runs in **open mode** (single-user dev) and these routes
  are still accessible; a warning is logged on first use.

Failure policy
--------------
Read endpoints raise 4xx/5xx as appropriate. The ``/start`` endpoint
returns 502 if the Gateway is unreachable; CAL writes by skills are
fire-and-forget per ``cal.py`` policy.
"""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import cal

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Settings / auth
# ---------------------------------------------------------------------------

_SECRETS_PATH = Path(os.path.expanduser("~/.hermes/kol-ops-bridge/secrets.yaml"))


def _load_bridge_key() -> Optional[str]:
    """Resolve the bridge API key from env or secrets file."""
    env = os.environ.get("HERMES_KOL_OPS_BRIDGE_KEY")
    if env:
        return env.strip() or None
    if _SECRETS_PATH.exists():
        # Tiny YAML parse — avoid pulling in PyYAML if not already loaded.
        for raw in _SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                if k.strip() == "bridge_api_key":
                    val = v.strip().strip("\"'")
                    return val or None
    return None


_BRIDGE_KEY_WARNED = False


def _require_bridge_key(provided: Optional[str]) -> None:
    """Enforce the bridge API key for mutating endpoints.

    In open mode (no key configured), allow the request but emit a
    single WARNING on first use so the dev knows to set the key
    before exposing the port externally.
    """
    global _BRIDGE_KEY_WARNED
    expected = _load_bridge_key()
    if not expected:
        if not _BRIDGE_KEY_WARNED:
            log.warning(
                "kol-ops-bridge: no API key configured "
                "(set HERMES_KOL_OPS_BRIDGE_KEY or %s); running in OPEN MODE",
                _SECRETS_PATH,
            )
            _BRIDGE_KEY_WARNED = True
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid bridge api key")


def _gateway_base_url() -> str:
    return os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642").rstrip("/")


def _gateway_api_key() -> Optional[str]:
    return os.environ.get("API_SERVER_KEY") or None


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class StartCampaignBody(BaseModel):
    """Body for POST /campaigns/{campaign_id}/start.

    ``brief`` is the orchestrator prompt text composed by the Web
    backend from the user's form input. The bridge forwards it
    verbatim to the Gateway as the ``input``.
    """

    brief: str = Field(..., min_length=1, description="Orchestrator user prompt")
    product_sku: str = Field(..., min_length=1)
    triggered_by: str = Field(default="web", pattern="^(chat|web|cron)$")
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    session_id: Optional[str] = None
    instructions: Optional[str] = Field(
        default=None,
        description="Optional system prompt override (rarely needed)",
    )


class ApproveShortlistBody(BaseModel):
    campaign_id: str
    decision: str = Field(..., pattern="^(approve_all|reject_all|approve_subset|reshuffle)$")
    selected_handles: list[str] = Field(default_factory=list)
    note: Optional[str] = None
    actor: str = Field(default="web")


class ManualAliasBody(BaseModel):
    kol_identity_id: int
    kind: str = Field(..., pattern="^(gmail_thread_id|gmail_message_id|email|handle)$")
    value: str = Field(..., min_length=1)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class ContractStubBody(BaseModel):
    kol_identity_id: int
    card_id: Optional[str] = None
    campaign_id: Optional[str] = None
    sub_status: str = Field(..., pattern="^(pending|sent_for_signature|signed|declined)$")
    signed_at: Optional[str] = None
    signed_url: Optional[str] = None
    note: Optional[str] = None
    actor: str = Field(default="web:operator")
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class LogisticsStubBody(BaseModel):
    kol_identity_id: int
    card_id: Optional[str] = None
    campaign_id: Optional[str] = None
    sub_status: str = Field(
        ...,
        pattern="^(pending|address_collected|tracking_filled|in_transit|delivered)$",
    )
    address: Optional[str] = None
    carrier: Optional[str] = None
    tracking_no: Optional[str] = None
    shipped_at: Optional[str] = None
    delivered_at: Optional[str] = None
    note: Optional[str] = None
    actor: str = Field(default="web:operator")
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class ContentVerdictBody(BaseModel):
    kol_identity_id: int
    card_id: Optional[str] = None
    campaign_id: Optional[str] = None
    verdict: str = Field(..., pattern="^(approve|revise)$")
    video_url: Optional[str] = None
    version: int = Field(default=1, ge=1)
    revision_notes: Optional[str] = None
    actor: str = Field(default="web:operator")
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class EscalationDecisionBody(BaseModel):
    escalation_id: int
    human_decision: str = Field(..., min_length=1)
    human_note: Optional[str] = None
    actor: str = Field(default="web:operator")


# ---------------------------------------------------------------------------
# READ endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
def health():
    """Liveness probe. Also reports schema version and DB path."""
    from .schema import SCHEMA_VERSION
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "db_path": str(cal.db_path()),
        "bridge_key_configured": _load_bridge_key() is not None,
        "gateway_url": _gateway_base_url(),
    }


@router.get("/identities")
def list_identities(env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$")):
    return {"identities": cal.list_identities(env=env)}


@router.get("/identities/{identity_id}")
def get_identity(identity_id: int):
    ident = cal.get_identity(identity_id)
    if not ident:
        raise HTTPException(status_code=404, detail="identity not found")
    return ident


@router.get("/identities/{identity_id}/timeline")
def get_timeline(
    identity_id: int,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
):
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return cal.list_timeline(identity_id, env=env)


@router.get("/drafts/pending")
def drafts_pending(env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$")):
    """Drafts awaiting human review (no sent_at yet)."""
    return {"drafts": cal.list_drafts_pending_review(env=env)}


@router.get("/drafts/{draft_id}")
def get_draft(
    draft_id: str,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
):
    """Full draft + context_snapshot — the data source for the
    Web "generation rationale" panel."""
    d = cal.get_draft(draft_id, env=env)
    if not d:
        raise HTTPException(status_code=404, detail="draft not found")
    return d


@router.get("/escalations/open")
def escalations_open(env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$")):
    return {"escalations": cal.list_escalations_open(env=env)}


@router.get("/events/recent")
def events_recent(
    limit: int = Query(default=100, ge=1, le=1000),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
):
    return {"events": cal.list_recent_events(limit=limit, env=env)}


@router.get("/events/latest-id")
def events_latest_id(env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$")):
    """Cursor for clients that want to long-poll for new events."""
    return {"latest_event_id": cal.latest_event_id(env=env)}


# ---------------------------------------------------------------------------
# Identity alias (manual binding from Web)
# ---------------------------------------------------------------------------


@router.post("/identities/aliases")
def add_alias_endpoint(
    body: ManualAliasBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(body.kol_identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    alias_id = cal.add_alias(
        kol_identity_id=body.kol_identity_id,
        kind=body.kind,
        value=body.value,
        source="manual_web",
        env=body.env,
    )
    cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type="alias_added",
        actor="web:operator",
        env=body.env,
        payload={"kind": body.kind, "value": body.value},
    )
    return {"alias_id": alias_id}


# ---------------------------------------------------------------------------
# Stub stage advancement (contract / logistics) — Web pushes these
# ---------------------------------------------------------------------------


@router.post("/contract/update")
def contract_update(
    body: ContractStubBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Stub: Web records contract sub-status. No DocuSign call.
    Schema/UI remain provider-agnostic for the future real adapter."""
    _require_bridge_key(x_bridge_key)
    payload = {
        "sub_status": body.sub_status,
        "signed_at": body.signed_at,
        "signed_url": body.signed_url,
        "note": body.note,
    }
    cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type=f"contract_{body.sub_status}",
        actor=body.actor,
        card_id=body.card_id,
        campaign_id=body.campaign_id,
        stage="contract",
        sub_status=body.sub_status,
        payload=payload,
        env=body.env,
    )
    return {"ok": True, "stage": "contract", "sub_status": body.sub_status}


@router.post("/logistics/update")
def logistics_update(
    body: LogisticsStubBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Stub: Web records logistics sub-status (address / tracking).
    No carrier API call."""
    _require_bridge_key(x_bridge_key)
    payload = body.model_dump(exclude={"kol_identity_id", "actor", "env"})
    cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type=f"logistics_{body.sub_status}",
        actor=body.actor,
        card_id=body.card_id,
        campaign_id=body.campaign_id,
        stage="logistics",
        sub_status=body.sub_status,
        payload=payload,
        env=body.env,
    )
    return {"ok": True, "stage": "logistics", "sub_status": body.sub_status}


# ---------------------------------------------------------------------------
# Content verdict — Web pushes pass/revise after reviewing the video
# ---------------------------------------------------------------------------


@router.post("/content/verdict")
def content_verdict(
    body: ContentVerdictBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Operator's verdict after reviewing the KOL's submitted video.

    ``approve`` → records ``content_approved`` event; the orchestrator
    (or a future close-out skill) is responsible for advancing the
    card to ``closed``.

    ``revise`` → records ``content_revision_requested`` with the
    operator's notes; the ``kol-outreach-content-revision`` skill is
    the consumer that drafts the follow-up email.
    """
    _require_bridge_key(x_bridge_key)
    if body.verdict == "approve":
        event_type = "content_approved"
        sub_status = f"approved_v{body.version}"
    else:
        event_type = "content_revision_requested"
        sub_status = f"revision_requested_v{body.version}"
    cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type=event_type,
        actor=body.actor,
        card_id=body.card_id,
        campaign_id=body.campaign_id,
        stage="content_delivery",
        sub_status=sub_status,
        payload={
            "verdict": body.verdict,
            "video_url": body.video_url,
            "version": body.version,
            "revision_notes": body.revision_notes,
        },
        env=body.env,
    )
    return {"ok": True, "event_type": event_type}


# ---------------------------------------------------------------------------
# Escalation resolution
# ---------------------------------------------------------------------------


@router.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(
    escalation_id: int,
    body: EscalationDecisionBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    _require_bridge_key(x_bridge_key)
    if body.escalation_id != escalation_id:
        raise HTTPException(status_code=400, detail="escalation_id mismatch")
    # In-place update; we don't go through cal._safe_write because the
    # Web caller wants a real error if persistence fails.
    import sqlite3

    try:
        with cal._connect() as conn:  # noqa: SLF001 — same package.
            cur = conn.execute(
                """UPDATE escalation_history
                   SET human_decision=?, human_note=?, human_decided_at=datetime('now')
                   WHERE id=?""",
                (body.human_decision, body.human_note, escalation_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="escalation not found")
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"db error: {exc}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# /start — proxy to Hermes Gateway POST /v1/runs
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(
    campaign_id: str,
    body: StartCampaignBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Spawn an orchestrator agent run via Hermes Gateway.

    The bridge does NOT spawn subprocesses or import AIAgent in-process;
    it forwards to the Gateway's ``POST /v1/runs`` which is the canonical
    "start a run" entry point. The caller can then subscribe to
    ``GET {gateway}/v1/runs/{run_id}/events`` for SSE.
    """
    _require_bridge_key(x_bridge_key)

    instructions = body.instructions or (
        "You are running the kol-outreach-orchestrator-flow skill. "
        f"Campaign id: {campaign_id}. Product SKU: {body.product_sku}. "
        f"triggered_by={body.triggered_by}. env={body.env}. "
        "In web mode, do NOT wait for chat shortlist approval — pause and exit "
        "after writing the shortlist; the operator will approve via the bridge API."
    )

    payload: dict[str, Any] = {
        "input": body.brief,
        "instructions": instructions,
        "session_id": body.session_id or f"kol-{campaign_id}",
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = _gateway_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    url = f"{_gateway_base_url()}/v1/runs"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        log.error("kol-ops-bridge: gateway unreachable at %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"gateway unreachable: {exc}")

    if resp.status_code >= 400:
        log.warning("kol-ops-bridge: gateway returned %s: %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail=f"gateway error {resp.status_code}: {resp.text}")

    data = resp.json() if resp.content else {}
    run_id = data.get("id") or data.get("run_id")
    return {
        "run_id": run_id,
        "session_id": payload["session_id"],
        "events_url": f"{_gateway_base_url()}/v1/runs/{run_id}/events" if run_id else None,
        "gateway_response": data,
    }


# ---------------------------------------------------------------------------
# TEST-data cleanup
# ---------------------------------------------------------------------------


@router.post("/admin/wipe-test")
def wipe_test(
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Delete every CAL row tagged env=TEST. No-op for LIVE."""
    _require_bridge_key(x_bridge_key)
    result = cal.wipe_env("TEST")
    return {"ok": True, "deleted": result}
