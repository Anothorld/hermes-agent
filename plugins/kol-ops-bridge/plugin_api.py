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
from . import gmail_client as _gmail

log = logging.getLogger(__name__)

router = APIRouter()


def _resolve_recipient(identity: dict[str, Any], env: str) -> Optional[str]:
    """Pick which email address a draft should be sent to.

    TEST mode honours ``KOL_OPS_BRIDGE_TEST_INBOX`` so operators can
    safely flush all drafts to their own inbox without ever risking a
    real outbound email. LIVE mode requires a real ``primary_email``
    on the identity row.
    """
    if env == "TEST":
        override = os.environ.get("KOL_OPS_BRIDGE_TEST_INBOX")
        if override:
            return override
    return identity.get("primary_email") or None


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


async def _spawn_gateway_run(
    *,
    session_id: str,
    instructions: str,
    user_input: str,
) -> dict[str, Any]:
    """Fire-and-forget POST to Gateway ``/v1/runs``; return the response JSON.

    Shared by ``/campaigns/{id}/start``, ``/approve-shortlist`` and
    ``/replies/inbound`` so the agent re-invocation contract is identical
    across triggers.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = _gateway_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "input": user_input,
        "instructions": instructions,
        "session_id": session_id,
    }
    url = f"{_gateway_base_url()}/v1/runs"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        log.error("kol-ops-bridge: gateway unreachable at %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"gateway unreachable: {exc}")
    if resp.status_code >= 400:
        log.warning("kol-ops-bridge: gateway returned %s: %s", resp.status_code, resp.text)
        raise HTTPException(
            status_code=502,
            detail=f"gateway error {resp.status_code}: {resp.text}",
        )
    data = resp.json() if resp.content else {}
    return data


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


class AgentIdentityBody(BaseModel):
    """Agent-side identity registration (called via `terminal` from a skill).

    The orchestrator agent in ``web`` mode has no MCP access to ``cal.py``
    helpers, so we expose a tiny HTTP shim. Calling this both upserts the
    identity AND emits a ``discovered`` event so the operator console sees
    the campaign moving immediately.
    """

    handle: str = Field(..., min_length=1)
    platform: str = Field(default="instagram", min_length=1)
    display_name: Optional[str] = None
    primary_email: Optional[str] = None
    region: Optional[str] = None
    creator_type: Optional[str] = None
    product_sku: Optional[str] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    actor: str = Field(default="agent")


class AgentEventBody(BaseModel):
    """Agent-side event emission (stage transitions, status changes).

    Use after :class:`AgentIdentityBody` to flip the visible stage on the
    operator console. ``kol_identity_id`` must reference a row created via
    ``upsert`` (the FK is enforced).
    """

    kol_identity_id: int = Field(..., gt=0)
    event_type: str = Field(..., min_length=1)
    stage: Optional[str] = None
    sub_status: Optional[str] = None
    product_sku: Optional[str] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    actor: str = Field(default="agent")
    payload: Optional[dict[str, Any]] = None


class AgentDraftBody(BaseModel):
    """Agent-side Gmail-draft stub (the agent has no Gmail MCP in dev).

    The agent records a draft in CAL with a synthetic ``draft_id`` so the
    operator console's draft queue renders the email. ``stage`` should be one
    of ``initial``, ``product_pick``, ``negotiation``, ``content_followup``.
    Emits a paired ``*_drafted`` event so the run timeline reflects progress.
    """

    kol_identity_id: int = Field(..., gt=0)
    stage: str = Field(..., pattern="^(initial|product_pick|negotiation|content_followup)$")
    subject: str = Field(..., min_length=1, max_length=512)
    body: str = Field(..., min_length=1)
    draft_id: Optional[str] = None
    gmail_message_id: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    product_sku: Optional[str] = None
    context_snapshot: Optional[dict[str, Any]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    actor: str = Field(default="agent")


class ApproveShortlistRequest(BaseModel):
    """Operator approval of the agent-produced shortlist.

    The console POSTs this after the user clicks "Approve & continue".
    The bridge records an ``approved`` event for each handle (or all
    registered handles when ``selected_handles`` is empty), then kicks off
    a follow-up agent run scoped to drafting the initial outreach emails.
    """

    selected_handles: list[str] = Field(default_factory=list)
    note: Optional[str] = None
    actor: str = Field(default="web:operator")
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    triggered_by: str = Field(default="web", pattern="^(chat|web|cron)$")


class InboundReplyBody(BaseModel):
    """Inject a simulated inbound reply (replaces the Gmail-poller path).

    Records a ``kol_reply_history`` row, emits ``reply_received``, then
    kicks off a follow-up agent run to classify the reply intent and
    draft a contextual response (product pitch / negotiation / decline ack).
    """

    kol_identity_id: int = Field(..., gt=0)
    body: str = Field(..., min_length=1, max_length=16_000)
    intent_hint: Optional[str] = Field(
        default=None,
        pattern="^(interested|asking_fee|decline|out_of_office|spam|unknown)$",
        description="Optional operator hint; if omitted the agent classifies.",
    )
    from_addr: Optional[str] = None
    gmail_message_id: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    product_sku: Optional[str] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    actor: str = Field(default="web:operator")
    triggered_by: str = Field(default="web", pattern="^(chat|web|cron)$")


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
# Agent-side observability shim (orchestrator skill calls these via curl)
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/identities")
def agent_register_identity(
    campaign_id: str,
    body: AgentIdentityBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Upsert a KOL identity AND emit a ``discovered`` event for this campaign.

    Returns ``{"identity_id": <int>}`` so the agent can chain subsequent
    ``/events`` calls. Idempotent on ``(platform, handle, env)`` per
    ``cal.upsert_identity``.
    """
    _require_bridge_key(x_bridge_key)
    identity_id = cal.upsert_identity(
        handle=body.handle,
        platform=body.platform,
        display_name=body.display_name,
        primary_email=body.primary_email,
        region=body.region,
        creator_type=body.creator_type,
        env=body.env,
    )
    if identity_id is None:
        raise HTTPException(status_code=500, detail="upsert_identity failed")
    cal.record_event(
        kol_identity_id=identity_id,
        event_type="discovered",
        stage="discovered",
        actor=body.actor,
        campaign_id=campaign_id,
        product_sku=body.product_sku,
        env=body.env,
    )
    return {"identity_id": identity_id, "campaign_id": campaign_id}


@router.post("/campaigns/{campaign_id}/events")
def agent_record_event(
    campaign_id: str,
    body: AgentEventBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Record a stage / status event for a campaign-bound KOL identity."""
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(body.kol_identity_id):
        raise HTTPException(
            status_code=404,
            detail=f"identity {body.kol_identity_id} not found",
        )
    event_id = cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type=body.event_type,
        actor=body.actor,
        campaign_id=campaign_id,
        product_sku=body.product_sku or _lookup_campaign_sku(campaign_id, body.env),
        stage=body.stage,
        sub_status=body.sub_status,
        payload=body.payload,
        env=body.env,
    )
    return {"event_id": event_id, "campaign_id": campaign_id}


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

    bridge_base = "http://127.0.0.1:8080/api/plugins/kol-ops-bridge"
    instructions = body.instructions or (
        "You are running the kol-outreach-orchestrator-flow skill. "
        f"Campaign id: {campaign_id}. Product SKU: {body.product_sku}. "
        f"triggered_by={body.triggered_by}. env={body.env}. "
        "\n\nMANDATORY OBSERVABILITY CONTRACT — read this BEFORE doing anything else.\n"
        "You do NOT have MCP access to the kol-ops-bridge `cal` helpers. You DO\n"
        "have the `terminal` tool. The operator console derives every visible\n"
        "piece of state from bridge CAL events — a run with zero events is shown\n"
        "as FAILED regardless of files written.\n\n"
        "Use these two HTTP endpoints from the terminal tool (no auth in dev):\n\n"
        "1) Register a discovered KOL (call this for EACH shortlisted handle,\n"
        "   BEFORE writing any file beyond an initial skill_view):\n"
        f"     curl -s -X POST {bridge_base}/campaigns/{campaign_id}/identities \\\n"
        "       -H 'Content-Type: application/json' \\\n"
        "       -d '{\"handle\":\"<ig_handle>\",\"platform\":\"instagram\","
        f"\"display_name\":\"<name>\",\"env\":\"{body.env}\","
        f"\"product_sku\":\"{body.product_sku}\",\"actor\":\"agent\"}}'\n"
        "   -> returns {\"identity_id\": N}. Save N for the events call.\n\n"
        "2) Emit a stage transition event AFTER each stage change\n"
        "   (outreach / product_pick / negotiation / contract / logistics /\n"
        "   content_delivery / closed):\n"
        f"     curl -s -X POST {bridge_base}/campaigns/{campaign_id}/events \\\n"
        "       -H 'Content-Type: application/json' \\\n"
        "       -d '{\"kol_identity_id\": N, \"event_type\":\"stage_changed\","
        f"\"stage\":\"outreach\",\"env\":\"{body.env}\","
        f"\"product_sku\":\"{body.product_sku}\",\"actor\":\"agent\"}}'\n\n"
        "HARD RULES:\n"
        "- After ONE skill_view, your NEXT 1-3 actions MUST be the `identities`\n"
        "  POST(s) above. Do not write_file before at least one identity is\n"
        "  registered.\n"
        "- If discovery turns up zero candidates, register a single placeholder\n"
        "  identity with handle='no_match_<campaign>' and emit a\n"
        "  `stage_changed` event with stage='closed', then exit.\n"
        "- DO NOT claim in your final response that you 'emitted record_event' or\n"
        "  'upserted an identity' unless the corresponding curl actually returned\n"
        "  HTTP 200 with an identity_id / event_id in the terminal output.\n\n"
        "WEB-MODE SHORTLIST HAND-OFF (THIS RUN):\n"
        "- After ALL discovered KOLs have been registered via /identities, emit\n"
        "  exactly ONE `shortlist_ready` event against the FIRST registered\n"
        "  identity. The payload MUST include a `candidates` array so the web\n"
        "  reviewer can score and pick — NOT just handles. Example:\n"
        f"     curl -s -X POST {bridge_base}/campaigns/{campaign_id}/events \\\n"
        "       -H 'Content-Type: application/json' \\\n"
        "       -d '{\"kol_identity_id\": <first_id>, \"event_type\":\"shortlist_ready\","
        "\"stage\":\"discovered\",\"sub_status\":\"awaiting_approval\","
        f"\"env\":\"{body.env}\","
        f"\"product_sku\":\"{body.product_sku}\",\"actor\":\"agent\","
        "\"payload\":{\n"
        "        \"candidates\": [\n"
        "          {\"handle\":\"h1\",\"platform\":\"instagram\","
        "\"audience_fit\":82,\"brand_safety\":95,"
        "\"engagement_quality\":74,\"niche_match\":88,"
        "\"reason\":\"50k IG, 4.2% ER, weekly cork-mat content, "
        "no fast-fashion sponsors\"},\n"
        "          {\"handle\":\"h2\",\"platform\":\"instagram\","
        "\"audience_fit\":71,\"brand_safety\":80,"
        "\"engagement_quality\":66,\"niche_match\":79,\"reason\":\"...\"}\n"
        "        ]}}'\n"
        "  Each score is an integer 0..100. Justify each pick concretely\n"
        "  (follower count, ER, recent topics, prior sponsor red flags).\n"
        "- Then STOP and exit the run. Do NOT draft outreach emails in this\n"
        "  run. The operator will approve in the web console, which triggers a\n"
        "  separate follow-up run with explicit drafting instructions."
    )

    session_id = body.session_id or f"kol-{campaign_id}"
    data = await _spawn_gateway_run(
        session_id=session_id,
        instructions=instructions,
        user_input=body.brief,
    )
    run_id = data.get("id") or data.get("run_id")
    return {
        "run_id": run_id,
        "session_id": session_id,
        "events_url": f"{_gateway_base_url()}/v1/runs/{run_id}/events" if run_id else None,
        "gateway_response": data,
    }


# ---------------------------------------------------------------------------
# /approve-shortlist  — operator approves agent's shortlist + triggers drafts
# ---------------------------------------------------------------------------


def _lookup_campaign_sku(campaign_id: str, env: str) -> Optional[str]:
    """Return the product_sku previously associated with this campaign, if any."""
    try:
        with cal._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT product_sku FROM kol_conversation_events "
                "WHERE campaign_id=? AND env=? AND product_sku IS NOT NULL "
                "ORDER BY id ASC LIMIT 1",
                (campaign_id, env),
            ).fetchone()
            return row["product_sku"] if row else None
    except Exception:  # noqa: BLE001
        return None


@router.get("/campaigns/{campaign_id}/shortlist")
def get_campaign_shortlist(
    campaign_id: str,
    env: str = "TEST",
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Return the latest ``shortlist_ready`` payload enriched with identity rows.

    The agent stores candidates as ``{handle, platform, audience_fit,
    brand_safety, engagement_quality, niche_match, reason}`` under
    ``payload.candidates``. We join each candidate handle against the
    ``kol_identity`` table so the UI can render an ``identity_id`` link
    (legacy payloads with only ``handles`` are normalised into candidates
    with null scores).
    """
    _require_bridge_key(x_bridge_key)

    import json as _json

    try:
        with cal._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT id, ts, kol_identity_id, payload_json "
                "FROM kol_conversation_events "
                "WHERE campaign_id=? AND env=? AND event_type='shortlist_ready' "
                "ORDER BY id DESC LIMIT 1",
                (campaign_id, env),
            ).fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"no shortlist_ready event for campaign {campaign_id}",
                )
            payload: dict = {}
            if row["payload_json"]:
                try:
                    payload = _json.loads(row["payload_json"]) or {}
                except Exception:  # noqa: BLE001
                    payload = {}
            candidates_raw = payload.get("candidates")
            if not candidates_raw and isinstance(payload.get("handles"), list):
                candidates_raw = [
                    {"handle": h, "platform": "instagram"}
                    for h in payload["handles"]
                ]
            candidates_raw = candidates_raw or []
            # Join handles -> identity rows (id, display_name, platform).
            ident_rows = conn.execute(
                """SELECT DISTINCT i.id AS id, i.handle AS handle,
                          i.platform AS platform, i.display_name AS display_name
                   FROM kol_identity i
                   JOIN kol_conversation_events e ON e.kol_identity_id = i.id
                   WHERE e.campaign_id = ? AND e.env = ?""",
                (campaign_id, env),
            ).fetchall()
            ident_by_handle = {r["handle"]: dict(r) for r in ident_rows}
            enriched = []
            for c in candidates_raw:
                handle = c.get("handle")
                ident = ident_by_handle.get(handle, {})
                enriched.append({
                    "handle": handle,
                    "platform": c.get("platform") or ident.get("platform"),
                    "identity_id": ident.get("id"),
                    "display_name": ident.get("display_name"),
                    "audience_fit": c.get("audience_fit"),
                    "brand_safety": c.get("brand_safety"),
                    "engagement_quality": c.get("engagement_quality"),
                    "niche_match": c.get("niche_match"),
                    "reason": c.get("reason"),
                })
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {exc}")

    return {
        "campaign_id": campaign_id,
        "env": env,
        "shortlist_event_id": row["id"],
        "shortlist_ts": row["ts"],
        "candidates": enriched,
    }


@router.post("/campaigns/{campaign_id}/approve-shortlist")
async def approve_shortlist(
    campaign_id: str,
    body: ApproveShortlistRequest,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Approve the shortlist and trigger the initial-draft follow-up run.

    Selection semantics:

    - Empty ``selected_handles`` -> approve EVERY identity already registered
      against this campaign (the common smoke-test path).
    - Non-empty -> approve only the listed handles; any unknown handle is
      reported back in ``unknown_handles``.

    For each approved identity we emit an ``approved`` CAL event so the
    console's stage progress bar advances out of ``discovered``. Then a
    follow-up agent run is queued so the orchestrator drafts initial emails.
    """
    _require_bridge_key(x_bridge_key)

    import sqlite3

    try:
        with cal._connect() as conn:  # noqa: SLF001 — same package.
            rows = conn.execute(
                """SELECT DISTINCT i.id AS id, i.handle AS handle, i.platform AS platform
                   FROM kol_identity i
                   JOIN kol_conversation_events e ON e.kol_identity_id = i.id
                   WHERE e.campaign_id = ? AND e.env = ?""",
                (campaign_id, body.env),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"db error: {exc}")

    available = {r["handle"]: dict(r) for r in rows}
    if not available:
        raise HTTPException(
            status_code=409,
            detail=f"no identities registered for campaign {campaign_id} (env={body.env})",
        )

    if body.selected_handles:
        unknown = [h for h in body.selected_handles if h not in available]
        approved = [available[h] for h in body.selected_handles if h in available]
    else:
        unknown = []
        approved = list(available.values())

    if not approved:
        raise HTTPException(
            status_code=400,
            detail={"message": "no handle matched", "unknown_handles": unknown},
        )

    product_sku = _lookup_campaign_sku(campaign_id, body.env)
    for ident in approved:
        cal.record_event(
            kol_identity_id=ident["id"],
            event_type="approved",
            stage="discovered",
            sub_status="approved",
            actor=body.actor,
            campaign_id=campaign_id,
            product_sku=product_sku,
            env=body.env,
            payload={"note": body.note} if body.note else None,
        )

    bridge_base = "http://127.0.0.1:8080/api/plugins/kol-ops-bridge"
    handles_block = "\n".join(
        f"  - id={a['id']} handle={a['handle']} platform={a['platform']}"
        for a in approved
    )
    instructions = (
        "FOLLOW-UP RUN -- initial outreach drafting.\n"
        f"Campaign id: {campaign_id}. env={body.env}.\n\n"
        "The operator has approved the shortlist. Your ONLY job in this run is\n"
        "to draft an initial outreach email for EACH approved KOL listed below,\n"
        "and emit an `outreach` stage_changed event per KOL. You do NOT have a\n"
        "Gmail MCP server -- use the bridge draft shim instead:\n\n"
        f"  curl -s -X POST {bridge_base}/campaigns/{campaign_id}/drafts \\\n"
        "    -H 'Content-Type: application/json' \\\n"
        "    -d '{\"kol_identity_id\": N, \"stage\":\"initial\","
        "\"subject\":\"<short subject>\",\"body\":\"<email body>\","
        f"\"env\":\"{body.env}\",\"actor\":\"agent\"}}'\n"
        "  -> returns {\"draft_id\": \"d-...\"}.\n\n"
        "Approved KOLs:\n"
        f"{handles_block}\n\n"
        "RULES:\n"
        "- One curl draft PER approved KOL. The draft endpoint AUTO-emits the\n"
        "  `initial_drafted` event, so you do NOT need a separate /events call\n"
        "  after each draft.\n"
        "- Subjects max 80 chars; bodies 80-400 words, no emoji spam, no links\n"
        "  beyond the product URL.\n"
        "- DO NOT claim success in your final response unless the curls returned\n"
        "  HTTP 200 with a draft_id.\n"
        "- After all KOLs handled, exit. Do not wait for replies."
    )
    user_input = (
        f"Draft initial outreach emails for the approved shortlist on "
        f"campaign {campaign_id}."
    )
    session_id = f"kol-{campaign_id}-drafts"
    data = await _spawn_gateway_run(
        session_id=session_id,
        instructions=instructions,
        user_input=user_input,
    )
    run_id = data.get("id") or data.get("run_id")
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "approved_count": len(approved),
        "approved_identity_ids": [a["id"] for a in approved],
        "unknown_handles": unknown,
        "run_id": run_id,
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# /drafts  — agent-side Gmail-draft shim
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/drafts")
def agent_record_draft(
    campaign_id: str,
    body: AgentDraftBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Stub Gmail-draft creation -> record in CAL + emit ``*_drafted`` event."""
    _require_bridge_key(x_bridge_key)
    identity = cal.get_identity(body.kol_identity_id)
    if not identity:
        raise HTTPException(
            status_code=404,
            detail=f"identity {body.kol_identity_id} not found",
        )
    import secrets as _secrets

    draft_id = body.draft_id or f"d-{_secrets.token_hex(8)}"
    product_sku = body.product_sku or _lookup_campaign_sku(campaign_id, body.env)

    # ------------------------------------------------------------------
    # Attempt Gmail draft push. Best-effort: failures fall back to the
    # legacy "stub" behaviour so the console still gets a CAL row.
    # ------------------------------------------------------------------
    gmail_message_id = body.gmail_message_id
    gmail_thread_id = body.gmail_thread_id
    gmail_push_status: dict[str, Any] = {"attempted": False}
    if not gmail_message_id:
        recipient = _resolve_recipient(identity, body.env)
        client = _gmail.default_client()
        if recipient and client.is_available():
            gmail_push_status = {"attempted": True, "recipient": recipient}
            try:
                result = client.create_draft(
                    to=recipient,
                    subject=body.subject,
                    body=body.body,
                )
                gmail_message_id = result.message_id or None
                gmail_thread_id = result.thread_id or None
                gmail_push_status.update(
                    ok=True,
                    draft_id=result.draft_id,
                    message_id=result.message_id,
                    thread_id=result.thread_id,
                )
                # Index the thread so inbound replies can be linked back.
                if gmail_thread_id:
                    cal.add_alias(
                        kol_identity_id=body.kol_identity_id,
                        kind="gmail_thread_id",
                        value=gmail_thread_id,
                        source="dispatcher",
                        env=body.env,
                    )
                if recipient:
                    cal.add_alias(
                        kol_identity_id=body.kol_identity_id,
                        kind="email",
                        value=recipient,
                        source="dispatcher",
                        env=body.env,
                    )
            except _gmail.GmailUnavailable as exc:
                log.warning("[bridge] gmail draft push failed: %s", exc)
                gmail_push_status.update(ok=False, error=str(exc))
        else:
            gmail_push_status = {
                "attempted": False,
                "reason": (
                    "no recipient resolvable for env=TEST (set "
                    "KOL_OPS_BRIDGE_TEST_INBOX) or identity has no "
                    "primary_email"
                    if not recipient
                    else "google_token.json missing"
                ),
            }

    snapshot = body.context_snapshot or {
        "stage": body.stage,
        "stub": gmail_message_id is None,
        "note": (
            "pushed to Gmail" if gmail_message_id
            else "agent-generated; no Gmail push (see gmail_push_status)"
        ),
        "gmail_push_status": gmail_push_status,
    }
    cal.record_draft(
        kol_identity_id=body.kol_identity_id,
        stage=body.stage,
        draft_id=draft_id,
        context_snapshot=snapshot,
        actor=body.actor,
        triggered_by="web",
        campaign_id=campaign_id,
        product_sku=product_sku,
        sub_status="pending_review",
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
        subject=body.subject,
        body=body.body,
        env=body.env,
    )
    cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type=f"{body.stage}_drafted",
        stage="outreach" if body.stage == "initial" else body.stage,
        sub_status="pending_review",
        actor=body.actor,
        campaign_id=campaign_id,
        product_sku=product_sku,
        env=body.env,
        payload={
            "draft_id": draft_id,
            "subject": body.subject,
            "gmail_message_id": gmail_message_id,
            "gmail_thread_id": gmail_thread_id,
        },
    )
    return {
        "draft_id": draft_id,
        "campaign_id": campaign_id,
        "gmail_message_id": gmail_message_id,
        "gmail_thread_id": gmail_thread_id,
        "gmail_push": gmail_push_status,
    }


# ---------------------------------------------------------------------------
# /replies/inbound  — operator-injected reply (replaces Gmail poller)
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/replies/inbound")
async def inbound_reply(
    campaign_id: str,
    body: InboundReplyBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
):
    """Record a simulated reply and queue a follow-up agent run."""
    _require_bridge_key(x_bridge_key)
    ident = cal.get_identity(body.kol_identity_id)
    if not ident:
        raise HTTPException(
            status_code=404,
            detail=f"identity {body.kol_identity_id} not found",
        )

    import datetime as _dt
    import secrets as _secrets

    received_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    gmail_message_id = body.gmail_message_id or f"sim-msg-{_secrets.token_hex(6)}"
    gmail_thread_id = body.gmail_thread_id or f"sim-thr-{_secrets.token_hex(6)}"

    product_sku = body.product_sku or _lookup_campaign_sku(campaign_id, body.env)
    cal.record_reply(
        kol_identity_id=body.kol_identity_id,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
        received_at=received_at,
        match_strategy="manual_inject",
        match_confidence=1.0,
        from_addr=body.from_addr,
        snippet=(body.body[:160] if body.body else None),
        body=body.body,
        intent=body.intent_hint,
        confidence=(0.99 if body.intent_hint else None),
        campaign_id=campaign_id,
        env=body.env,
    )
    cal.record_event(
        kol_identity_id=body.kol_identity_id,
        event_type="reply_received",
        stage="outreach",
        sub_status="reply_received",
        actor=body.actor,
        campaign_id=campaign_id,
        product_sku=product_sku,
        env=body.env,
        payload={
            "gmail_message_id": gmail_message_id,
            "intent_hint": body.intent_hint,
        },
    )

    bridge_base = "http://127.0.0.1:8080/api/plugins/kol-ops-bridge"
    hint_clause = (
        f"The operator hinted '{body.intent_hint}', trust that unless the\n"
        "   text clearly contradicts it.\n\n"
        if body.intent_hint
        else "Make your own classification.\n\n"
    )
    instructions = (
        "REPLY HANDLING RUN.\n"
        f"Campaign id: {campaign_id}. env={body.env}. "
        f"KOL identity_id={body.kol_identity_id} (handle={ident['handle']}).\n\n"
        "An inbound reply was just received (full text in your user input below).\n\n"
        "STEP 1: Classify the intent as one of: interested, asking_fee, decline,\n"
        f"out_of_office, spam, unknown. {hint_clause}"
        "STEP 2: Emit a `reply_classified` event with the chosen intent:\n"
        f"  curl -s -X POST {bridge_base}/campaigns/{campaign_id}/events \\\n"
        "    -H 'Content-Type: application/json' \\\n"
        "    -d '{\"kol_identity_id\": "
        + str(body.kol_identity_id)
        + ", \"event_type\":\"reply_classified\","
        "\"stage\":\"outreach\",\"sub_status\":\"<intent>\","
        f"\"env\":\"{body.env}\",\"actor\":\"agent\","
        "\"payload\":{\"intent\":\"<intent>\",\"confidence\":<0..1>}}'\n\n"
        "STEP 3: If intent is `interested` -> draft a product_pick follow-up.\n"
        "If `asking_fee` -> draft a negotiation follow-up. Use:\n"
        f"  curl -s -X POST {bridge_base}/campaigns/{campaign_id}/drafts \\\n"
        "    -H 'Content-Type: application/json' \\\n"
        "    -d '{\"kol_identity_id\": "
        + str(body.kol_identity_id)
        + ", \"stage\":\"product_pick\","  # agent overrides to 'negotiation' if needed
        "\"subject\":\"<subject>\",\"body\":\"<body>\","
        f"\"env\":\"{body.env}\",\"actor\":\"agent\"}}'\n"
        "Set \"stage\":\"negotiation\" for fee-asking replies. For `decline` /\n"
        "`out_of_office` / `spam`, SKIP the draft and instead emit a\n"
        "`stage_changed` event with stage='closed' sub_status='no_reply_needed'.\n\n"
        "STEP 4: For interested/asking_fee, emit a final `stage_changed` event\n"
        "with the new stage so the console advances.\n\n"
        "RULES: terse, no apology padding, do NOT claim success without verified\n"
        "curl 200 responses."
    )
    user_input = (
        f"Inbound reply from KOL {ident['handle']} "
        f"(identity_id={body.kol_identity_id}) on campaign {campaign_id}:\n\n"
        f"---\n{body.body}\n---"
    )
    session_id = f"kol-{campaign_id}-reply-{gmail_message_id}"
    data = await _spawn_gateway_run(
        session_id=session_id,
        instructions=instructions,
        user_input=user_input,
    )
    run_id = data.get("id") or data.get("run_id")
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "kol_identity_id": body.kol_identity_id,
        "gmail_message_id": gmail_message_id,
        "run_id": run_id,
        "session_id": session_id,
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
