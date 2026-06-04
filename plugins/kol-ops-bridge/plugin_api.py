"""KOL Ops Bridge — HTTP API (v2.4 goal-driven surface).

Mounted at ``/api/plugins/kol-ops-bridge/``. The endpoint surface is
governed by Phase A3 of the v2.4 refactor plan; legacy stage-driven
routes (``/contract/update`` etc.) have been removed.

Auth model: dashboard session token via mount middleware; mutating
routes additionally require ``X-Bridge-Key`` (env
``HERMES_KOL_OPS_BRIDGE_KEY`` or ``~/.hermes/kol-ops-bridge/secrets.yaml``).
A missing key in dev triggers "open mode" with a one-shot warning.

The legacy ``/campaigns/{id}/start`` orchestrator-launch endpoint is
intentionally absent: it will return alongside the rewritten
``kol-outreach-orchestrator-flow`` skill in Phase B.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Mapping, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi import Path as FastAPIPath
from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

from . import cal
from . import campaign_validation
from . import confirmed_ingest
from . import confirmed_fact_buffer
from . import discovery_router
from . import classifier_facts
from . import dispatch_router
from . import policies as _policies
from . import pricing_engine
from . import reply_draft
from . import orphan_gmail_draft
from . import learning_distill
from . import learning_jobs
from . import learning_job_store
from . import learning_overview
from . import learning_promote
from . import learning_store
from . import reply_diff
from . import reject_tags
from .gmail_reconcile import (
    backfill_edit_learning_all_mailboxes,
    run_reconcile_all_mailboxes,
    run_reconcile_sent,
)
from . import email_conversation
from . import mailbox_resolver
from .gmail_client import GmailClient, GmailUnavailable
from .gmail_console import (
    default_operator_user_id,
    multi_operator_gmail_enabled,
    resolve_console_user_id,
)
from . import gmail_reply_envelope
from .schema import FACT_NAMESPACES, GOAL_NAMES, SCHEMA_VERSION

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_SECRETS_PATH = Path(os.path.expanduser("~/.hermes/kol-ops-bridge/secrets.yaml"))
_OPEN_MODE_WARNED = False


def _load_bridge_key() -> Optional[str]:
    env = os.environ.get("HERMES_KOL_OPS_BRIDGE_KEY")
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
            log.warning("kol-ops-bridge: no API key configured — running in open mode (dev only)")
            _OPEN_MODE_WARNED = True
        return
    if provided is None or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="invalid or missing X-Bridge-Key")


# ---------------------------------------------------------------------------
# Shared input normalisation
# ---------------------------------------------------------------------------

# Callers that build URLs / JSON bodies via untyped string interpolation
# (notably the web console's React layer, where ``encodeURIComponent(null)``
# renders the 4-char string ``"null"``) have historically leaked sentinel
# strings into the bridge. Treat these as if the caller had omitted the
# field — for bodies/query params we coerce them to ``None``; for path
# params (``/campaigns/{campaign_id}/...``) the bridge raises 400 instead
# (a missing campaign id along a campaign-scoped path is unrecoverable).
_NULL_SENTINELS: frozenset[str] = frozenset({"null", "undefined", "nan", "none"})


def _norm_campaign_id(value: Any) -> Optional[str]:
    """Coerce ``"null"`` / ``"undefined"`` / ``""`` and friends to None.

    Use as a Pydantic ``@field_validator(..., mode="before")`` on every
    ``campaign_id`` body / query field. The strip+lower-case sentinel
    set covers JS ``encodeURIComponent(null|undefined|NaN)`` and Python
    ``str(None)`` accidents alike.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.lower() in _NULL_SENTINELS:
        return None
    return s


def _reject_null_sentinel_campaign_id(campaign_id: str) -> str:
    """Path-param guard for ``/campaigns/{campaign_id}/...`` routes.

    These routes are campaign-scoped by definition; passing a sentinel
    string means the caller has a bug (most commonly a frontend that
    interpolated a null/undefined into the URL). Fail closed with a
    machine-readable 400 so the console can surface a clear error
    instead of getting a misleading "campaign not found" 404 downstream.
    """
    if campaign_id.strip().lower() in _NULL_SENTINELS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_campaign_id",
                "message": (
                    f"campaign_id path segment is the sentinel string "
                    f"{campaign_id!r} — the caller built this URL from a "
                    f"null/undefined value. Provide a real campaign_id."
                ),
            },
        )
    return campaign_id


def _campaign_id_path_dep(
    campaign_id: str = FastAPIPath(...),
) -> str:
    """FastAPI dependency: validates the ``{campaign_id}`` path segment.

    Apply via ``Annotated[str, Depends(_campaign_id_path_dep)]`` on any
    handler whose URL template contains ``/{campaign_id}/...``.
    """
    return _reject_null_sentinel_campaign_id(campaign_id)


def _campaign_id_query_required_dep(
    campaign_id: str = Query(..., min_length=1),
) -> str:
    """FastAPI dependency: validates a required ``?campaign_id=`` query.

    Mirrors the path-segment guard for the handful of routes that take
    campaign_id as a required query string (dispatch-context, lanes
    view, etc.) instead of a path segment.
    """
    return _reject_null_sentinel_campaign_id(campaign_id)


def _campaign_id_query_optional_dep(
    campaign_id: Optional[str] = Query(default=None),
) -> Optional[str]:
    """FastAPI dependency: normalises an optional ``?campaign_id=`` query.

    Silently coerces sentinel strings (``"null"``, ``"undefined"``, ...)
    and empty strings to ``None`` so callers get the same semantics as
    if the param had been omitted entirely.
    """
    return _norm_campaign_id(campaign_id)


# ---------------------------------------------------------------------------
# Pydantic bodies
# ---------------------------------------------------------------------------


class _CampaignIdNormaliserMixin(BaseModel):
    """Mixin: any subclass declaring a ``campaign_id`` field gets the
    sentinel-string → None coercion applied in ``mode="before"``.

    ``check_fields=False`` lets the validator live on the mixin even
    though it does not declare the field itself. Pydantic v2 wires it
    up at the subclass level only when ``campaign_id`` is actually
    declared.
    """

    @field_validator("campaign_id", mode="before", check_fields=False)
    @classmethod
    def _coerce_null_string_campaign_id(cls, v: Any) -> Optional[str]:
        return _norm_campaign_id(v)


class IdentityUpsertBody(BaseModel):
    primary_handle: str
    platform: str = "instagram"
    primary_email: Optional[str] = None
    display_name: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None
    contact_role: str = "kol"
    default_shipping_address: Optional[dict[str, Any]] = None
    default_payment_method: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")

    @field_validator("primary_email", mode="before")
    @classmethod
    def _validate_primary_email(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if not _EMAIL_RE.match(s):
            raise ValueError(
                f"primary_email must look like 'x@y.tld'; got {v!r}. "
                "If this is a link-in-bio URL, personal website, or brand "
                "name, store it as identity.linktree_url / "
                "identity.personal_site_url via write-facts-multi instead."
            )
        return s.lower()


class CampaignConfigUpsertBody(BaseModel):
    label: Optional[str] = None
    product_display_name: Optional[str] = None
    product_url: Optional[str] = None
    product_unit_price: Optional[float] = None
    barter_policy: Optional[str] = None
    paid_ceiling: Optional[float] = None
    paid_target_budget: Optional[float] = None
    commission_band: Optional[dict[str, Any]] = None
    deliverable_platforms: Optional[list[str]] = None
    deliverable_count_per_platform: Optional[int] = None

    @field_validator("deliverable_count_per_platform", mode="before")
    @classmethod
    def _normalize_deliverable_count_per_platform(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            return campaign_validation.normalize_deliverable_count_per_platform(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    extra_notes: Optional[str] = None
    brief_template_id: Optional[str] = None
    sku_whitelist: Optional[list[str]] = None
    variant_candidates: Optional[list[dict[str, Any]]] = None
    color_variant_policy: Optional[str] = None
    audit_standards_md: Optional[str] = None
    test_mode_to: Optional[str] = None
    followup_intervals: Optional[dict[str, Any]] = None
    contract_required: Optional[bool] = None
    status: Optional[str] = None
    nox_quota_enabled: Optional[bool] = None
    nox_monthly_budget: Optional[int] = Field(default=None, ge=0, le=2000)
    nox_supplement_enabled: Optional[bool] = None
    nox_supplement_max_calls: Optional[int] = Field(default=None, ge=0, le=200)
    nox_cache_enabled: Optional[bool] = None
    nox_cache_retain_months: Optional[int] = Field(default=None, ge=0, le=120)
    nox_cache_timezone: Optional[str] = Field(default=None, max_length=64)
    nox_diligence_dimensions: Optional[list[str]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class CandidateUpsertBody(BaseModel):
    identity_id: Optional[int] = None
    primary_handle: Optional[str] = None
    platform: str = "instagram"
    source: str
    discovery_score: Optional[float] = None
    payload: Optional[dict[str, Any]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class CandidateSelectBody(BaseModel):
    identity_ids: list[int]
    selected_by: str
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class CandidateStatusBody(BaseModel):
    identity_ids: list[int]
    candidate_status: str = Field(pattern="^(discovered|shortlisted|selected_for_outreach|needs_review|rejected|archived)$")
    review_reason: Optional[str] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class FactsWriteBody(_CampaignIdNormaliserMixin):
    campaign_id: Optional[str] = None
    namespace: str
    facts: dict[str, Any]
    source: str = "manual"
    source_event_id: Optional[int] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class EscalationOpenBody(_CampaignIdNormaliserMixin):
    identity_id: Optional[int] = None
    campaign_id: Optional[str] = None
    goal: Optional[str] = None
    reason: str
    severity: str = "normal"
    question_to_operator: Optional[str] = None
    parent_escalation_id: Optional[int] = None
    resume_context: Optional[dict[str, Any]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class EscalationResolveBody(BaseModel):
    decision: str
    decided_by: str
    operator_answer: Optional[str] = None
    operator_facts: Optional[dict[str, Any]] = None
    final_state: str = "resolved"


class ApprovalCorrectionBody(BaseModel):
    tags: list[str] = Field(default_factory=list)
    note: Optional[str] = None
    suggested_fix: Optional[str] = None


class ApprovalDecisionBody(_CampaignIdNormaliserMixin):
    identity_id: int
    campaign_id: Optional[str] = None
    decided_by: str
    operator_user_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="KOL Ops Console users.id for Gmail mailbox binding",
    )
    operator_email: Optional[str] = None
    note: Optional[str] = None
    correction: Optional[ApprovalCorrectionBody] = None
    extra_facts: Optional[dict[str, Any]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class MailboxTakeoverBody(_CampaignIdNormaliserMixin):
    campaign_id: str
    operator_user_id: int = Field(ge=1)
    operator_email: Optional[str] = None
    requester_role: str = Field(default="operator", min_length=1, max_length=32)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class ReconcileSentBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    lookback_days: int = Field(default=7, ge=1, le=90)
    max_results: int = Field(default=100, ge=1, le=500)


class BackfillEditLearningBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    dry_run: bool = False
    limit: int = Field(default=500, ge=1, le=2000)


class MarkReplyHandledBody(_CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    message_id: str = Field(min_length=1, max_length=256)
    identity_id: Optional[int] = Field(default=None, ge=1)
    campaign_id: Optional[str] = None
    detected_mailbox_user_id: Optional[int] = Field(default=None, ge=0)
    handled_label: str = Field(default="kol-outreach/handled", min_length=1, max_length=120)
    pending_label: str = Field(default="kol-outreach/pending-reply", min_length=1, max_length=120)


class ArchiveBody(_CampaignIdNormaliserMixin):
    # NOTE: campaign_id is required. The mixin coerces sentinel strings
    # to None first; Pydantic then rejects the resulting None against
    # this annotation, so a caller that sent ``campaign_id: "null"``
    # gets a 422 with a clear "field required" message instead of
    # silently writing under a phantom campaign.
    campaign_id: str
    outcome: str
    preferred_skus: Optional[list[str]] = None
    preferred_mode: Optional[str] = None
    avg_revision_rounds: Optional[float] = None
    negotiation_style: Optional[str] = Field(
        default=None,
        pattern="^(hard_anchor|soft_anchor|unknown)$",
    )
    delivery_quality: Optional[float] = None
    decided_by: str = "skill:archival-writer"


class RouteDiscoveryBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    selected_by: str = "agent"
    operator_note: str = ""


class IngestIdentityBody(BaseModel):
    primary_handle: str
    platform: str = "instagram"
    display_name: Optional[str] = None
    primary_email: Optional[str] = None

    @field_validator("primary_email", mode="before")
    @classmethod
    def _validate_primary_email(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if not _EMAIL_RE.match(s):
            raise ValueError(
                f"primary_email must look like 'x@y.tld'; got {v!r}"
            )
        return s.lower()


class IngestCandidateBody(BaseModel):
    source: str
    discovery_score: Optional[float] = None
    payload: Optional[dict[str, Any]] = None
    candidate_status: str = Field(
        default="discovered",
        pattern="^(discovered|shortlisted|selected_for_outreach|needs_review|rejected|archived)$",
    )


class IngestConfirmedCandidateBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    source: str
    ingest_id: Optional[str] = None
    identity: IngestIdentityBody
    candidate: IngestCandidateBody
    identity_facts: Optional[dict[str, Any]] = None


class BufferConfirmedCandidateBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    source: str
    ingest_id: Optional[str] = None
    identity: IngestIdentityBody
    candidate: IngestCandidateBody
    identity_facts: Optional[dict[str, Any]] = None


class FactsWriteMultiBody(_CampaignIdNormaliserMixin):
    campaign_id: Optional[str] = None
    namespaces: dict[str, dict[str, Any]]
    source: str = "skill"
    source_event_id: Optional[int] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    signals: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Classifier signals for the same inbound turn. Required for "
            "deterministic committed-key sanitization when source is email:…"
        ),
    )


class PolicyPutBody(BaseModel):
    content_md: str
    updated_by: str
    owner_user_id: Optional[int] = None
    title: Optional[str] = None


class PolicyRollbackBody(BaseModel):
    to_version: int = Field(ge=1)
    updated_by: str = Field(min_length=1, max_length=120)
    owner_user_id: Optional[int] = None
    env: Optional[str] = Field(default=None, pattern="^(TEST|LIVE)$")


class EventWriteBody(_CampaignIdNormaliserMixin):
    identity_id: int
    event_type: str
    actor: str
    campaign_id: Optional[str] = None
    goal: Optional[str] = None
    lane: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


# ---------------------------------------------------------------------------
# Health + admin
# ---------------------------------------------------------------------------


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "db_path": str(cal.db_path()),
        "fact_namespaces": list(FACT_NAMESPACES),
        "goals": list(GOAL_NAMES),
        "bridge_key_configured": _load_bridge_key() is not None,
    }


@router.post("/admin/wipe-test")
def wipe_test(
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Hard-cut rebuild. Drops + re-creates every CAL object.
    Refuses unless the bridge key is set (no auth = no destructive ops).
    """
    if _load_bridge_key() is None:
        raise HTTPException(status_code=403, detail="open-mode bridge cannot wipe")
    _require_bridge_key(x_bridge_key)
    cal.hard_reset()
    return {"ok": True, "schema_version": SCHEMA_VERSION}


@router.post("/admin/check-stuck-goals")
def admin_check_stuck_goals(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Cron-callable scanner. Emits a DingTalk notification per goal whose
    ``updated_at`` exceeds the campaign's ``followup_intervals[goal]``
    (defaults to 72h). Returns the matched rows so the caller can audit.
    """
    _require_bridge_key(x_bridge_key)
    stuck = cal.check_stuck_goals(env=env)
    return {"env": env, "count": len(stuck), "stuck": stuck}


# ---------------------------------------------------------------------------
# Identities + relationship
# ---------------------------------------------------------------------------


@router.post("/identities")
def upsert_identity(
    body: IdentityUpsertBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    iid = cal.upsert_identity(**body.model_dump(exclude_none=True))
    if iid is None:
        raise HTTPException(status_code=500, detail="upsert_identity failed")
    return {"identity_id": iid}


@router.get("/identities/{identity_id}")
def get_identity(identity_id: int) -> dict[str, Any]:
    ident = cal.get_identity(identity_id)
    if not ident:
        raise HTTPException(status_code=404, detail="identity not found")
    return ident


@router.get("/identities/{identity_id}/relationship")
def get_relationship(identity_id: int) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    rel = cal.get_relationship(identity_id)
    if rel is None:
        return {
            "identity_id": identity_id,
            "total_collabs": 0,
            "collab_history": [],
            "preferred_skus": [],
        }
    return rel


@router.get("/identities/{identity_id}/collab-history")
def get_collab_history(identity_id: int) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {"identity_id": identity_id, "items": cal.list_collab_history(identity_id)}


@router.get("/relationships")
def list_archived_kols(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    q: Optional[str] = Query(default=None, max_length=200),
    last_outcome: Optional[str] = Query(default=None, max_length=60),
    platform: Optional[str] = Query(default=None, max_length=40),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List KOL identities with ``total_collabs > 0``, joined with the
    relationship summary. Used by the console's KOL archive view to
    browse past collaborations across the entire pool.
    """
    return cal.list_archived_kols(
        env=env, q=q, last_outcome=last_outcome, platform=platform,
        limit=limit, offset=offset,
    )


@router.get("/identities/{identity_id}/relationship/reusable-facts")
def get_reusable_facts(identity_id: int) -> dict[str, Any]:
    """Reusable identity-level facts wrapped in a stable envelope.

    Shape: ``{"identity_id": int, "facts": {...}}``. The inner ``facts``
    dict is whatever ``cal.get_reusable_facts`` chooses to expose
    (currently a curated subset of identity + relationship rows).
    Consumers must NOT assume top-level keys other than these two.
    """
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {
        "identity_id": identity_id,
        "facts": cal.get_reusable_facts(identity_id),
    }


@router.get("/identities/{identity_id}/goals")
def get_goal_state(
    identity_id: int,
    campaign_id: Annotated[str, Depends(_campaign_id_query_required_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {"goals": cal.get_goal_state(identity_id=identity_id,
                                        campaign_id=campaign_id, env=env)}


@router.get("/identities/{identity_id}/timeline")
def get_identity_timeline(
    identity_id: int,
    campaign_id: Annotated[Optional[str], Depends(_campaign_id_query_optional_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Reverse-chronological event timeline for a single KOL identity."""
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {
        "identity_id": identity_id,
        "events": cal.list_events(
            env=env,
            identity_id=identity_id,
            campaign_id=campaign_id,
            limit=limit,
        ),
    }


def _operator_user_id_header(
    x_koc_operator_user_id: Optional[int] = Header(
        default=None, alias="X-KOC-Operator-User-Id",
    ),
) -> Optional[int]:
    if x_koc_operator_user_id is None or x_koc_operator_user_id < 1:
        return None
    return int(x_koc_operator_user_id)


def _mailbox_http_error(exc: mailbox_resolver.MailboxError) -> HTTPException:
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
    }
    if isinstance(exc, mailbox_resolver.MailboxAccessDeniedError):
        binding = getattr(exc, "bound_email", None)
        if binding:
            detail["mailbox_email"] = binding
    return HTTPException(status_code=exc.status_code, detail=detail)


def _gmail_client_for_inbound_labels(body: MarkReplyHandledBody) -> GmailClient:
    """Resolve the operator mailbox that owns the inbound Gmail message."""
    if multi_operator_gmail_enabled():
        missing = []
        if body.identity_id is None:
            missing.append("identity_id")
        if not body.campaign_id:
            missing.append("campaign_id")
        if body.detected_mailbox_user_id is None:
            missing.append("detected_mailbox_user_id")
        if missing:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "mailbox_context_required",
                    "message": (
                        "multi-operator Gmail requires "
                        + ", ".join(missing)
                        + " on mark-reply-handled / unmark-reply-handled"
                    ),
                },
            )
    if body.identity_id is not None and body.campaign_id:
        try:
            return mailbox_resolver.resolve_for_inbound_gmail(
                identity_id=body.identity_id,
                campaign_id=body.campaign_id,
                env=body.env,
                detected_mailbox_user_id=body.detected_mailbox_user_id,
            )
        except mailbox_resolver.MailboxError as exc:
            raise _mailbox_http_error(exc) from exc
    if body.detected_mailbox_user_id is not None and body.detected_mailbox_user_id >= 0:
        client = mailbox_resolver.client_for_user(
            body.detected_mailbox_user_id if body.detected_mailbox_user_id > 0 else None,
        )
        if not client.is_available():
            raise HTTPException(
                status_code=503,
                detail="gmail token or google_api.py unavailable for detected mailbox",
            )
        return client
    client = GmailClient()
    if not client.is_available():
        raise HTTPException(status_code=503, detail="gmail token or google_api.py unavailable")
    return client


@router.get("/identities/{identity_id}/email-conversation")
def get_email_conversation(
    identity_id: int,
    campaign_id: Annotated[str, Depends(_campaign_id_query_required_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    operator_user_id: Annotated[Optional[int], Depends(_operator_user_id_header)] = None,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Gmail sent/received messages for the KOL detail communication panel.

    Outbound rows are limited to messages in the Gmail ``SENT`` label (final
    sends). Draft composer state is excluded.
    """
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    try:
        resolved = mailbox_resolver.resolve_for_read(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            operator_user_id=operator_user_id,
        )
    except mailbox_resolver.MailboxError as exc:
        raise _mailbox_http_error(exc) from exc
    binding_payload = None
    if resolved.binding:
        binding_payload = {
            "user_id": resolved.binding.user_id,
            "email": resolved.binding.email,
            "bound_at": resolved.binding.bound_at,
        }
    return email_conversation.build_email_conversation_safe(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        client=resolved.client,
        mailbox_binding=binding_payload,
    )


@router.post("/identities/{identity_id}/mailbox/takeover")
def takeover_campaign_mailbox(
    identity_id: int,
    body: MailboxTakeoverBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    try:
        binding = mailbox_resolver.takeover_mailbox(
            identity_id=identity_id,
            campaign_id=body.campaign_id,
            env=body.env,
            new_operator_user_id=body.operator_user_id,
            operator_email=str(body.operator_email or ""),
            source=f"web:takeover:{body.operator_user_id}",
            requester_role=str(body.requester_role or "operator"),
        )
    except mailbox_resolver.MailboxError as exc:
        raise _mailbox_http_error(exc) from exc
    return {
        "ok": True,
        "mailbox": {
            "user_id": binding.user_id,
            "email": binding.email,
            "bound_at": binding.bound_at,
        },
    }


@router.get("/events/recent")
def get_recent_events(
    campaign_id: Annotated[Optional[str], Depends(_campaign_id_query_optional_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    since_id: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Recent conversation events across all identities (optionally a single
    campaign).  ``since_id`` supports incremental pulls (web SSE / cron
    pollers); omit it to get the latest page in reverse-chronological order.
    """
    return {
        "events": cal.list_events(
            env=env,
            campaign_id=campaign_id,
            since_id=since_id,
            limit=limit,
        ),
    }


@router.post("/events")
def post_event(
    body: EventWriteBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Append a row to ``kol_conversation_events``.

    Used by the gmail reply poller + skills that need to record a
    deterministic event without going through goal recompute first.
    """
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(body.identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    event_id = cal.write_event(
        identity_id=body.identity_id,
        event_type=body.event_type,
        actor=body.actor,
        campaign_id=body.campaign_id,
        goal=body.goal,
        lane=body.lane,
        payload=body.payload,
        env=body.env,
    )
    if event_id is None:
        raise HTTPException(status_code=500, detail="write_event failed")
    return {"event_id": event_id}


@router.post("/identities/{identity_id}/archive")
def archive_collab(
    identity_id: int,
    body: ArchiveBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    cal.archive_collab(
        identity_id=identity_id,
        campaign_id=body.campaign_id,
        outcome=body.outcome,
        preferred_skus=body.preferred_skus,
        preferred_mode=body.preferred_mode,
        avg_revision_rounds=body.avg_revision_rounds,
        delivery_quality=body.delivery_quality,
        negotiation_style=body.negotiation_style,
        decided_by=body.decided_by,
    )
    cal.recompute_goals(identity_id=identity_id, campaign_id=body.campaign_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Campaigns + candidates
# ---------------------------------------------------------------------------


@router.put("/campaigns/{campaign_id}")
def upsert_campaign_config(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: CampaignConfigUpsertBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    cal.upsert_campaign_config(campaign_id=campaign_id,
                               **body.model_dump(exclude_none=True))
    return {"ok": True, "campaign_id": campaign_id}


class CampaignParseBody(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


@router.post("/campaigns/parse")
def parse_campaign_intent(
    body: CampaignParseBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Free-text → ``campaign_config`` draft (no DB write).

    Deterministic regex-driven shim — covers the common Chinese/English
    operator phrasings ("预算 1500", "IG 5 / TT 5", "commission 12%",
    "测试收件 johnny@..."). The frontend wizard previews the result and
    asks the operator to confirm / edit before calling
    ``PUT /campaigns/{id}``. Unrecognised fields are returned in
    ``unparsed_lines`` so the operator still sees their input.
    """
    return _parse_campaign_text(body.text)


class FactsFromTextBody(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    appended_by: str = Field(min_length=1, max_length=120)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


@router.post("/campaigns/{campaign_id}/facts-from-text")
def append_campaign_facts_from_text(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: FactsFromTextBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Append a free-text note to ``campaign_config.extra_notes``.

    Used by the Campaign Wizard's ``extra_notes`` 区域 so operators can
    drop ad-hoc context onto an existing campaign without overwriting
    structured fields. The note is timestamped + signed for audit.
    """
    cfg = cal.get_campaign_config(campaign_id, env=body.env)
    if not cfg:
        raise HTTPException(status_code=404, detail="campaign not found")
    existing = (cfg.get("extra_notes") or "").rstrip()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    appended = f"\n\n---\n[{stamp} by {body.appended_by}]\n{body.text.strip()}"
    cal.upsert_campaign_config(
        campaign_id=campaign_id,
        env=body.env,
        extra_notes=(existing + appended).lstrip(),
    )
    return {"ok": True, "campaign_id": campaign_id, "appended_at": stamp}


# ----- helpers for /campaigns/parse ---------------------------------------

_PLATFORM_ALIASES = {
    "ig": "instagram", "instagram": "instagram", "insta": "instagram",
    "tt": "tiktok", "tiktok": "tiktok",
    "yt": "youtube", "youtube": "youtube",
    "xhs": "xiaohongshu", "rednote": "xiaohongshu",
}


def _parse_campaign_text(text: str) -> dict[str, Any]:
    """Best-effort regex parser for operator briefs.

    Recognises (case-insensitive):
    - 预算 / budget <amount> [总 / total / 单 / per]
    - IG 5 / TikTok 3 / xhs 2 → deliverable_platforms + count
    - commission 12% / 抽成 12% → commission_band
    - paid ceiling 800 / 上限 800 → paid_ceiling
    - 现金预算 / 付费预算 / target budget 500 → paid_target_budget
    - 测试收件 / test_mode_to <email>
    - 标签 / label <text>
    - 跑 <campaign_id>
    """
    import re

    raw = text.strip()
    out: dict[str, Any] = {}
    unparsed: list[str] = []

    def m(pattern: str, flags: int = re.IGNORECASE) -> Optional["re.Match[str]"]:
        return re.search(pattern, raw, flags)

    if (h := m(r"跑\s*([A-Za-z0-9\-_]+)")):
        out["campaign_id"] = h.group(1)
    if (h := m(r"\b(?:label|标签|名称)\s*[:：]?\s*([^\n,，；;]+)")):
        out["label"] = h.group(1).strip()

    if (h := m(r"\b(?:单价|unit\s*price)\s*[:：]?\s*\$?(\d+(?:\.\d+)?)")):
        out["product_unit_price"] = float(h.group(1))
    if (h := m(
        r"\b(?:paid[\s_-]*target(?:[\s_-]*budget)?|"
        r"现金预算|付费预算|target[\s_-]*budget|cash[\s_-]*target)\s*[:：]?\s*\$?(\d+(?:\.\d+)?)"
    )):
        out["paid_target_budget"] = float(h.group(1))
    if (h := m(r"\b(?:paid[\s_-]*ceiling|预算上限|上限|cap)\s*[:：]?\s*\$?(\d+(?:\.\d+)?)")):
        out["paid_ceiling"] = float(h.group(1))
    elif (h := m(r"\b(?:预算|budget)\s*[:：]?\s*\$?(\d+(?:\.\d+)?)")):
        out["paid_target_budget"] = float(h.group(1))

    if (h := m(r"\b(?:commission|抽成|分成)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%\s*(?:[-–~至到]\s*(\d+(?:\.\d+)?)\s*%)?")):
        lo = float(h.group(1))
        hi = float(h.group(2)) if h.group(2) else lo
        out["commission_band"] = {"min": lo / 100, "max": hi / 100}

    platforms: list[str] = []
    counts: list[int] = []
    for alias, canonical in _PLATFORM_ALIASES.items():
        # Match "IG 5", "instagram x 5", "instagram*5", "instagram：5"
        match = re.search(
            rf"\b{alias}\b[\s xX×*：:]+(\d+)", raw, re.IGNORECASE,
        )
        if match and canonical not in platforms:
            platforms.append(canonical)
            counts.append(int(match.group(1)))
    if platforms:
        out["deliverable_platforms"] = platforms
        # Single uniform count if all equal; otherwise use the first
        if len(set(counts)) == 1:
            out["deliverable_count_per_platform"] = counts[0]
        else:
            out["deliverable_count_per_platform"] = counts[0]
            unparsed.append(
                "deliverable_count_per_platform varies per platform "
                f"({dict(zip(platforms, counts))}); applied first value"
            )

    if (h := m(r"\b(?:test[\s_-]*mode[\s_-]*to|测试收件|test\s*inbox)\s*[:：]?\s*([\w.+-]+@[\w-]+\.[\w.-]+)")):
        out["test_mode_to"] = h.group(1)

    if m(r"\bcontract[\s_-]*required\s*[:：]?\s*(false|no|不需要|不签)\b"):
        out["contract_required"] = False
    elif m(r"\b(?:不签合同|no\s+contract)\b"):
        out["contract_required"] = False

    if (h := m(r"\b(?:sku|SKU|whitelist|白名单)\s*[:：]?\s*((?:[A-Z]+[A-Z0-9_-]*)(?:\s*[,，、/]\s*[A-Z]+[A-Z0-9_-]*)*)")):
        skus = re.split(r"[,，、/]\s*", h.group(1))
        out["sku_whitelist"] = [s.strip() for s in skus if s.strip()]

    return {"parsed": out, "unparsed_lines": unparsed, "raw": raw}


@router.get("/campaigns")
def list_campaigns(
    env: Optional[str] = Query(default=None, pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Distinct (campaign_id, env) pairs known to the bridge with candidate
    counts. Powers the Web kanban's campaign picker."""
    return {"items": cal.list_campaigns(env=env)}


@router.get("/campaigns/{campaign_id}")
def get_campaign_config(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    env: Optional[str] = Query(default=None, pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    cfg = cal.get_campaign_config(campaign_id, env=env)
    if not cfg:
        raise HTTPException(status_code=404, detail="campaign not found")
    return cfg


@router.get("/campaigns/{campaign_id}/candidates")
def list_candidates(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    return {"candidates": cal.list_candidates(campaign_id, env=env)}


@router.get("/campaigns/{campaign_id}/candidate-handles")
def list_candidate_handles(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    items = cal.list_candidate_handles(campaign_id, env=env)
    handles = [item["handle"] for item in items if item.get("handle")]
    return {"handles": handles, "count": len(handles), "items": items}


@router.post("/campaigns/{campaign_id}/candidates")
def upsert_candidate(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: CandidateUpsertBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    iid = body.identity_id
    if iid is None:
        if not body.primary_handle:
            raise HTTPException(status_code=400,
                                detail="must provide identity_id OR primary_handle")
        iid = cal.upsert_identity(primary_handle=body.primary_handle,
                                  platform=body.platform, env=body.env)
    candidate_id = cal.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=iid,
        source=body.source,
        discovery_score=body.discovery_score,
        payload=body.payload,
        env=body.env,
    )
    return {"candidate_id": candidate_id, "identity_id": iid}


@router.post("/campaigns/{campaign_id}/candidates/resolve-relationships")
def resolve_relationships(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    n = cal.resolve_candidate_relationships(campaign_id=campaign_id, env=env)
    return {"resolved": n}


@router.post("/campaigns/{campaign_id}/candidates/select")
def select_candidates(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: CandidateSelectBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    n = cal.select_candidates_for_outreach(
        campaign_id=campaign_id, identity_ids=body.identity_ids,
        selected_by=body.selected_by, env=body.env,
    )
    return {"selected": n}


@router.post("/campaigns/{campaign_id}/candidates/status")
def set_candidate_status(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: CandidateStatusBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    n = cal.set_candidate_status(
        campaign_id=campaign_id,
        identity_ids=body.identity_ids,
        candidate_status=body.candidate_status,
        review_reason=body.review_reason,
        env=body.env,
    )
    return {"updated": n}


@router.post("/campaigns/{campaign_id}/candidates/route-discovery")
def route_discovery(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: RouteDiscoveryBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Deterministic Discovery → Outreach router.

    Resolves relationship_status for the campaign pool, then for each
    candidate still in ``candidate_status='discovered'``:
      - new_prospect → select for cold outreach + write
        ``identity.outreach_path='cold'``
      - repeat_kol → select for reengagement outreach + write
        ``identity.outreach_path='reengagement'``
      - repeat_kol_needs_review → open one ``reengagement_outreach``
        escalation
      - rejected → leave alone

    Idempotent: candidates already past ``discovered`` are reported as
    ``skipped_already_routed``.
    """
    _require_bridge_key(x_bridge_key)
    return discovery_router.route_discovery_pool(
        campaign_id=campaign_id,
        env=body.env,
        selected_by=body.selected_by,
        operator_note=body.operator_note,
    )


@router.post("/campaigns/{campaign_id}/ingest-confirmed-candidate")
def ingest_confirmed_candidate(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: IngestConfirmedCandidateBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Deterministic single-candidate ingest: identity → facts → candidate.

    Intended for agent/tool use immediately after a KOL candidate is confirmed
    from CDP/browser extraction. No LLM transformation — payload must be
    structured JSON matching the body schema.
    """
    _require_bridge_key(x_bridge_key)
    try:
        return confirmed_ingest.ingest_confirmed_candidate(
            campaign_id=campaign_id,
            env=body.env,
            source=body.source,
            identity=body.identity.model_dump(exclude_none=True),
            candidate=body.candidate.model_dump(exclude_none=True),
            identity_facts=body.identity_facts,
            ingest_id=body.ingest_id,
        )
    except confirmed_ingest.IngestValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/campaigns/{campaign_id}/buffer-confirmed-candidate")
def buffer_confirmed_candidate(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    body: BufferConfirmedCandidateBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Append a confirmed candidate to the local jsonl ingest buffer."""
    _require_bridge_key(x_bridge_key)
    payload = {
        "source": body.source,
        "identity": body.identity.model_dump(exclude_none=True),
        "candidate": body.candidate.model_dump(exclude_none=True),
        "identity_facts": body.identity_facts,
    }
    event = confirmed_fact_buffer.append_enqueue(
        path=confirmed_fact_buffer.default_buffer_path(),
        campaign_id=campaign_id,
        env=body.env,
        payload=payload,
        fact_id=body.ingest_id,
        identity_hint=body.identity.primary_handle,
    )
    return {"buffered": True, "event": event}


@router.post("/ingest-buffer/replay")
def replay_ingest_buffer(
    limit: int = Query(default=50, ge=1, le=500),
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Replay pending/failed buffered ingest events into CAL."""
    _require_bridge_key(x_bridge_key)
    return confirmed_fact_buffer.replay_pending(limit=limit)


@router.get("/ingest-buffer/pending")
def list_ingest_buffer_pending(
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """List pending/failed entries in the confirmed ingest buffer."""
    _require_bridge_key(x_bridge_key)
    buf = confirmed_fact_buffer.default_buffer_path()
    pending = confirmed_fact_buffer.list_pending(buf)
    return {"buffer_path": str(buf), "count": len(pending), "items": pending}


@router.get("/campaigns/{campaign_id}/lanes")
def get_lanes(
    campaign_id: Annotated[str, Depends(_campaign_id_path_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Return per-identity lane snapshots for the entire campaign.

    Output: ``{ "items": [ { "identity_id":..., "handle":...,
    "candidate_status":..., "relationship_status":...,
    "repeat_count":..., "last_outcome":..., "archived": bool,
    "lanes":{commerce:[...], ...} }, ... ],
    "counts": {"pending_approvals": N, "open_escalations": M,
    "pending_approvals_latest_at": iso|null,
    "open_escalations_latest_at": iso|null} }``.
    Suitable for the Web kanban lane filter + top-of-page badges
    (counts) and the per-card unread red-dots / Draft sub-state badges
    (per-item ``pending_approval_*`` / ``open_escalation_*`` /
    ``reply_draft_state`` fields).
    """
    candidates = cal.list_candidate_handles(campaign_id, env=env)
    identity_ids = [int(c["identity_id"]) for c in candidates if c.get("identity_id")]

    # Batch DB reads: one query per resource type instead of 4×N per KOL.
    relationships = cal.batch_relationship_summaries(identity_ids)
    facts_by_id = cal.batch_kanban_facts(
        campaign_id=campaign_id, identity_ids=identity_ids, env=env,
    )
    lanes_by_id = cal.batch_lanes_views_for_campaign(
        campaign_id, env=env, identity_ids=identity_ids,
    )

    # Single sweep over the campaign-scoped queues so per-card lookup
    # is O(1) and we avoid N+1 bridge calls. The kanban refreshes every
    # 20s + on every data-channel event, so this matters.
    approvals_by_id: dict[int, list[dict[str, Any]]] = {}
    approvals_latest_at: Optional[str] = None
    for a in cal.list_pending_approvals(env=env):
        if a.get("campaign_id") != campaign_id:
            continue
        iid = a.get("identity_id")
        if not isinstance(iid, int):
            continue
        approvals_by_id.setdefault(iid, []).append(a)
        captured = a.get("captured_at")
        if captured and (approvals_latest_at is None or captured > approvals_latest_at):
            approvals_latest_at = captured

    escalations_by_id: dict[int, list[dict[str, Any]]] = {}
    escalations_latest_at: Optional[str] = None
    for e in cal.list_escalations(state="awaiting_answer", env=env):
        if e.get("campaign_id") != campaign_id:
            continue
        iid = e.get("identity_id")
        if not isinstance(iid, int):
            continue
        escalations_by_id.setdefault(iid, []).append(e)
        created = e.get("created_at")
        if created and (escalations_latest_at is None or created > escalations_latest_at):
            escalations_latest_at = created

    items = []
    for c in candidates:
        if not c.get("identity_id"):
            continue
        iid = int(c["identity_id"])
        rel = relationships.get(iid) or {}
        facts = facts_by_id.get(iid) or {}
        handle = c.get("handle")
        if isinstance(handle, str):
            handle = handle.strip().lstrip("@") or None

        appr_rows = approvals_by_id.get(iid, [])
        appr_latest = max(
            (r.get("captured_at") for r in appr_rows if r.get("captured_at")),
            default=None,
        )
        esc_rows = escalations_by_id.get(iid, [])
        esc_latest = max(
            (r.get("created_at") for r in esc_rows if r.get("created_at")),
            default=None,
        )

        items.append({
            "identity_id": iid,
            "handle": handle or f"id{iid}",
            "candidate_status": c["candidate_status"],
            "relationship_status": c["relationship_status"],
            "repeat_count": int(rel.get("total_collabs") or 0),
            "last_outcome": rel.get("last_outcome"),
            "archived": c["candidate_status"] in ("archived", "rejected"),
            "lanes": lanes_by_id.get(iid, {}),
            "outreach_sent_at": facts.get("offer.outreach_sent_at"),
            "interest_signal": facts.get("offer.interest_signal"),
            # Tri-state we expose so the FE can distinguish "approved
            # but skill hasn't built a Gmail draft" from "draft sitting
            # in Gmail waiting on the operator to click Send":
            #   None / False      → no draft yet (operator may need to
            #                       re-trigger kol-cold-outreach)
            #   True              → Gmail draft created
            #   + outreach_sent_at → SENT reconcile confirmed delivery
            "outreach_draft_created": bool(facts.get("offer.outreach_draft_created")),
            "gmail_draft_id": facts.get("offer.gmail_draft_id"),
            "gmail_thread_id": facts.get("offer.gmail_thread_id"),
            # Per-card unread + Draft sub-state inputs (Phase D fix-2).
            "pending_approval_count": len(appr_rows),
            "pending_approval_latest_at": appr_latest,
            "open_escalation_count": len(esc_rows),
            "open_escalation_latest_at": esc_latest,
            "reply_draft_state": _reply_draft_state(facts),
        })
    counts = {
        "pending_approvals": sum(len(v) for v in approvals_by_id.values()),
        "open_escalations": sum(len(v) for v in escalations_by_id.values()),
        "pending_approvals_latest_at": approvals_latest_at,
        "open_escalations_latest_at": escalations_latest_at,
    }
    return {"items": items, "counts": counts}


def _reply_draft_state(facts: Mapping[str, Any]) -> Optional[str]:
    """Derive the Draft sub-state surfaced on the kanban card.

    Decision flow mirrors the cold-outreach + reply-dispatcher pipeline:
    once ``offer.outreach_sent`` flips true the draft has been delivered
    (covers both reply drafts and cold-outreach drafts). Before that, a
    pending ``approval.reply_draft`` means the operator still owes a
    decision in the approval queue; a decided-approved draft with a
    ``gmail_draft`` payload is sitting in Gmail waiting on Send. None
    means the card has no draft in flight.
    """
    if facts.get("offer.outreach_sent"):
        return "sent"
    reply = facts.get("approval.reply_draft")
    if isinstance(reply, dict):
        decision = reply.get("decision")
        if decision in (None, "pending"):
            return "pending"
        if decision == "approved" and isinstance(reply.get("gmail_draft"), dict):
            return "approved_unsent"
    return None


# ---------------------------------------------------------------------------
# Facts (per-identity write)
# ---------------------------------------------------------------------------


@router.post("/facts/{identity_id}")
def write_facts(
    identity_id: int,
    body: FactsWriteBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    campaign_id = body.campaign_id or _inherit_campaign_id_from_escalation(
        namespace=body.namespace, facts=body.facts,
    )
    try:
        n = cal.write_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespace=body.namespace,
            facts=body.facts,
            source=body.source,
            source_event_id=body.source_event_id,
            env=body.env,
        )
    except cal.FactNamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"written": n}


def _inherit_campaign_id_from_escalation(
    *, namespace: str, facts: dict[str, Any]
) -> Optional[str]:
    """Backstop for the draft-preview path: when an agent writes an
    ``approval.*`` fact carrying a ``linked_escalation_id`` but forgets
    to set ``campaign_id`` in the request body, look up the escalation
    row and return its campaign_id so the resulting fact inherits scope.
    Returns None when not applicable (non-approval namespace, no linked
    escalation, escalation not found, or escalation itself unscoped).
    """
    if namespace != "approval":
        return None
    for value in facts.values():
        if not isinstance(value, dict):
            continue
        linked = value.get("linked_escalation_id")
        if linked is None:
            continue
        try:
            escalation_id = int(linked)
        except (TypeError, ValueError):
            continue
        cid = cal.get_escalation_campaign_id(escalation_id)
        if cid:
            return cid
    return None


@router.post("/facts/{identity_id}/multi")
def write_facts_multi(
    identity_id: int,
    body: FactsWriteMultiBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Write facts across multiple namespaces in one call.

    Body shape: ``{"campaign_id":..., "source":..., "namespaces":
    {"<offer|identity|fulfillment|approval>": {"<ns>.<key>": <val>, ...}}}``.
    All namespaces are pre-validated; an invalid key aborts the whole call
    before any insert.
    """
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    campaign_id = body.campaign_id
    if not campaign_id:
        approval_facts = body.namespaces.get("approval")
        if isinstance(approval_facts, dict):
            campaign_id = _inherit_campaign_id_from_escalation(
                namespace="approval", facts=approval_facts,
            )
    namespaces = body.namespaces
    classifier_adjustments: list[str] = []
    if classifier_facts.should_sanitize_classifier_source(body.source):
        namespaces, classifier_adjustments = (
            classifier_facts.sanitize_classifier_namespaces(
                namespaces, body.signals,
            )
        )
        if classifier_adjustments:
            log.info(
                "classifier_fact_sanitize identity_id=%s source=%s adjustments=%s",
                identity_id,
                body.source,
                classifier_adjustments,
            )
    try:
        written = cal.write_facts_multi(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespaces=namespaces,
            source=body.source,
            source_event_id=body.source_event_id,
            env=body.env,
        )
    except cal.FactNamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    out: dict[str, Any] = {"written": written}
    if classifier_adjustments:
        out["classifier_sanitize"] = classifier_adjustments
    return out


@router.get("/identities/{identity_id}/dispatch-context")
def get_dispatch_context(
    identity_id: int,
    campaign_id: Annotated[str, Depends(_campaign_id_query_required_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Bundle the read snapshots ``kol-reply-dispatcher`` needs in one call.

    Returns ``{goals, lanes, relationship, reusable_facts, campaign_config,
    campaign_facts, identity_facts, candidate}`` for a single
    (identity, campaign) pair. Replaces 5 separate reads with 1.
    ``campaign_config`` is ``None`` if the campaign row is missing
    (caller must surface that as a routing error). ``campaign_facts``
    is the latest per-campaign fact snapshot (``offer.*``, etc.) from
    ``latest_facts_for(campaign_id=...)``.
    """
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    active_goals = _active_goal_names(
        identity_id=identity_id, campaign_id=campaign_id, env=env,
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        learning_hints = learning_store.build_learning_hints(
            conn, env=env, active_goals=active_goals,
        )
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "goals": cal.get_goal_state(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        "lanes": cal.get_lanes_view(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        "relationship": cal.get_relationship(identity_id),
        # Same shape as GET /relationship/reusable-facts:
        # ``{"identity_id":..., "facts":{...}}``.
        "reusable_facts": {
            "identity_id": identity_id,
            "facts": cal.get_reusable_facts(identity_id),
        },
        "learning_hints": learning_hints,
        "campaign_config": cal.get_campaign_config(campaign_id, env=env),
        # Latest campaign-scoped facts (offer.* negotiation state, etc.).
        "campaign_facts": cal.latest_facts_for(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        # Per-campaign discovery evidence written by the discovery skill into
        # ``campaign_candidates.payload_json`` (reason / niche_match /
        # showcase_evidence / conversion_mechanism). ``None`` when the
        # identity is not a candidate of this campaign.
        "candidate": cal.get_candidate_for(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        ),
        # All identity-level facts (campaign_id IS NULL), keyed as
        # ``identity.<key>`` — surfaces the creator-brief facts
        # (content_pillars, signature_hooks, voice_descriptors, hero_post_*,
        # recommendation_reason) plus any other identity-scoped fact written
        # via ``write-facts-multi``.
        "identity_facts": cal.latest_facts_for(
            identity_id=identity_id, campaign_id=None, env=env,
        ),
    }


@router.get("/identities/{identity_id}/reply-dispatch-status")
def get_reply_dispatch_status(
    identity_id: int,
    campaign_id: Annotated[str, Depends(_campaign_id_query_required_dep)],
    message_id: str = Query(..., min_length=1, max_length=256),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Idempotency probe for the Gmail reply poller (no mutation)."""
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return cal.reply_dispatch_status(
        identity_id=identity_id,
        campaign_id=campaign_id,
        message_id=message_id,
        env=env,
    )


@router.get("/identities/{identity_id}/reply-chase-hint")
def get_reply_chase_hint(
    identity_id: int,
    campaign_id: Annotated[str, Depends(_campaign_id_query_required_dep)],
    message_id: str = Query(..., min_length=1, max_length=256),
    thread_id: Optional[str] = Query(default=None, max_length=256),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Deterministic follow-up policy for one inbound Gmail message."""
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return cal.reply_chase_hint(
        identity_id=identity_id,
        campaign_id=campaign_id,
        message_id=message_id,
        thread_id=thread_id,
        env=env,
    )


@router.get("/facts/{identity_id}")
def read_facts(
    identity_id: int,
    campaign_id: Annotated[Optional[str], Depends(_campaign_id_query_optional_dep)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {"facts": cal.latest_facts_for(
        identity_id=identity_id, campaign_id=campaign_id, env=env,
    )}


class BatchFactsSubsetBody(_CampaignIdNormaliserMixin):
    """Batch-read latest fact keys for many identities in one campaign."""

    identity_ids: list[int] = Field(min_length=1, max_length=500)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    fact_keys: list[str] = Field(default_factory=list, max_length=32)


class BatchIdentityIdsBody(BaseModel):
    identity_ids: list[int] = Field(min_length=1, max_length=2000)


@router.post("/facts/batch-subset")
def batch_facts_subset(body: BatchFactsSubsetBody) -> dict[str, Any]:
    """Return ``{by_identity: {identity_id: {fact_key: value}}}`` for list UIs."""
    keys = tuple(body.fact_keys) if body.fact_keys else cal.KANBAN_FACT_KEYS
    by_id = cal.batch_latest_facts_subset(
        campaign_id=body.campaign_id,
        identity_ids=body.identity_ids,
        env=body.env,
        fact_keys=keys,
    )
    return {"by_identity": {str(iid): facts for iid, facts in by_id.items()}}


@router.post("/identities/briefs")
def batch_identity_briefs(body: BatchIdentityIdsBody) -> dict[str, Any]:
    """Minimal identity cards keyed by stringified id (console list enrichment)."""
    by_id = cal.batch_identity_briefs(body.identity_ids)
    return {
        "identities": {
            str(iid): brief for iid, brief in by_id.items()
        },
    }


# ---------------------------------------------------------------------------
# Approvals (cross-cutting view of approval.* facts)
# ---------------------------------------------------------------------------


@router.get("/approvals")
def list_approvals(
    status: str = Query(
        default="pending",
        pattern="^(pending|approved|rejected|all)$",
    ),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None, ge=1),
    campaign_id: Annotated[Optional[str], Depends(_campaign_id_query_optional_dep)] = None,
) -> dict[str, Any]:
    if status == "pending":
        return {
            "approvals": cal.list_pending_approvals(
                env=env, identity_id=identity_id, campaign_id=campaign_id,
            ),
        }
    return {
        "approvals": cal.list_decided_approvals(
            status=status,
            env=env,
            identity_id=identity_id,
            campaign_id=campaign_id,
        ),
    }


def _linked_escalation_id(value: Mapping[str, Any]) -> Optional[int]:
    raw_link = value.get("linked_escalation_id") or value.get("escalation_id")
    try:
        return int(raw_link) if raw_link is not None else None
    except (TypeError, ValueError):
        return None


def _mark_linked_reply_escalation_handled(
    *, escalation_id: int, env: str, decided_by: str
) -> Optional[int]:
    row = next(
        (r for r in cal.list_escalations(env=env) if r.get("id") == escalation_id),
        None,
    )
    if not row or row.get("state") not in {"awaiting_answer", "answered", "resuming"}:
        return None
    return cal.resolve_escalation(
        escalation_id=escalation_id,
        decision="resume",
        decided_by=decided_by,
        operator_answer="Linked approval.reply_draft was approved; escalation handled by draft approval.",
        final_state="resolved",
    )


def _active_goal_names(*, identity_id: int, campaign_id: str, env: str) -> list[str]:
    """Return goal names that are not terminal/inactive."""
    active_statuses = {"active", "blocked", "in_progress", "unsatisfied", "paused"}
    return [
        g["goal"]
        for g in cal.get_goal_state(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        )
        if g.get("status") in active_statuses
    ]


def _coalesce_operator_user_id(body: ApprovalDecisionBody) -> Optional[int]:
    """Resolve Console ``users.id`` for approve from body, env, or ``decided_by`` email."""
    if body.operator_user_id is not None and body.operator_user_id >= 1:
        return int(body.operator_user_id)
    default_uid = default_operator_user_id()
    if default_uid is not None:
        return default_uid
    if body.operator_email:
        resolved = resolve_console_user_id(email=body.operator_email)
        if resolved is not None:
            return resolved
    decided = (body.decided_by or "").strip()
    if decided.startswith(("web:", "cli:")) and "@" in decided:
        email = decided.split(":", 1)[1].strip()
        if email:
            return resolve_console_user_id(email=email)
    return None


def _record_draft_reject_learning(
    *,
    fact_path: str,
    body: ApprovalDecisionBody,
    approval_value: dict[str, Any],
    linked_escalation_id: Optional[int],
) -> Optional[int]:
    """Persist a structured reject-learning event for downstream few-shot."""
    if fact_path != "approval.reply_draft":
        return None
    correction = body.correction
    tags = reject_tags.normalize_reject_tags(
        correction.tags if correction else None,
    )
    note = (correction.note if correction and correction.note else body.note) or ""
    suggested_fix = (correction.suggested_fix if correction else None) or ""
    draft = approval_value.get("draft") if isinstance(approval_value.get("draft"), dict) else {}
    agent_body = str(draft.get("body") or "")
    goal = str(approval_value.get("primary_goal") or "")
    child_skill = str(approval_value.get("child_skill") or "")
    payload = {
        "fact_path": fact_path,
        "tags": tags,
        "note": note,
        "suggested_fix": suggested_fix,
        "agent_body": agent_body,
        "child_skill": child_skill,
        "goal": goal,
        "linked_escalation_id": linked_escalation_id,
    }
    return cal.write_event(
        identity_id=body.identity_id,
        campaign_id=body.campaign_id,
        event_type="draft_rejected_learning",
        goal=goal or None,
        lane=str(approval_value.get("primary_lane") or "") or None,
        actor=f"approval:{body.decided_by}",
        payload=payload,
        env=body.env,
    )


def _approve_or_reject(
    *, fact_path: str, decision: str, body: ApprovalDecisionBody
) -> dict[str, Any]:
    if not fact_path.startswith("approval."):
        raise HTTPException(status_code=400, detail="fact_path must start with 'approval.'")
    if not cal.get_identity(body.identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    # Identity-level approvals (e.g. style_learning_proposal) use campaign_id=NULL;
    # latest_facts_for must run even when campaign_id is omitted.
    previous_value = cal.latest_facts_for(
        identity_id=body.identity_id,
        campaign_id=body.campaign_id,
        env=body.env,
    ).get(fact_path)
    if isinstance(previous_value, dict):
        value: dict[str, Any] = dict(previous_value)
    elif previous_value is None:
        value = {}
    else:
        value = {"value": previous_value}
    linked_escalation_id: Optional[int] = _linked_escalation_id(value)
    handled_escalation_id: Optional[int] = None
    # Idempotent replay: a previous approve for this reply_draft already
    # created the Gmail draft and persisted decision=approved. The console
    # may retry (its httpx times out before the bridge's Gmail subprocess
    # does) — without this short-circuit each retry would create a new
    # Gmail draft, orphaning the previous one in Drafts.
    prior_draft = (
        previous_value.get("gmail_draft") if isinstance(previous_value, dict) else None
    )
    if (
        decision == "approved"
        and fact_path == "approval.reply_draft"
        and isinstance(previous_value, dict)
        and previous_value.get("decision") == "approved"
        and isinstance(prior_draft, dict)
        and prior_draft.get("draft_id")
    ):
        return {
            "ok": True,
            "decision": "approved",
            "derived_escalation_id": None,
            "linked_escalation_id": linked_escalation_id,
            "handled_escalation_id": None,
            "gmail_draft": prior_draft,
            "idempotent_replay": True,
        }
    # Idempotent replay: style/strategy batch already merged into policy.
    if (
        decision == "approved"
        and fact_path == learning_store.STYLE_LEARNING_APPROVAL_FACT
        and isinstance(previous_value, dict)
        and previous_value.get("decision") == "approved"
    ):
        return {
            "ok": True,
            "decision": "approved",
            "derived_escalation_id": None,
            "linked_escalation_id": linked_escalation_id,
            "handled_escalation_id": None,
            "gmail_draft": None,
            "learning_event_id": None,
            "style_policy_apply": previous_value.get("style_policy_apply"),
            "idempotent_replay": True,
        }
    gmail_draft: dict[str, Any] | None = None
    if decision == "approved" and fact_path == "approval.reply_draft":
        if not body.campaign_id:
            raise HTTPException(
                status_code=400,
                detail="campaign_id is required to approve approval.reply_draft",
            )
        operator_user_id = _coalesce_operator_user_id(body)
        if operator_user_id is None or operator_user_id < 1:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "operator_required",
                    "message": (
                        "operator_user_id is required to approve approval.reply_draft "
                        "(pass --operator-user-id, --operator-email, web:email decided_by, "
                        "or set KOC_DEFAULT_OPERATOR_USER_ID)"
                    ),
                },
            )
        try:
            resolved = mailbox_resolver.resolve_for_write(
                identity_id=body.identity_id,
                campaign_id=body.campaign_id,
                env=body.env,
                operator_user_id=operator_user_id,
                operator_email=str(body.operator_email or ""),
                source=f"approval:{body.decided_by}",
            )
        except mailbox_resolver.MailboxError as exc:
            raise _mailbox_http_error(exc) from exc
        gmail_draft = _create_gmail_draft_for_reply_approval(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            approval_value=value,
            env=body.env,
            client=resolved.client,
        )
        value["gmail_draft"] = gmail_draft
    style_policy_apply: Optional[dict[str, Any]] = None
    if (
        decision == "approved"
        and fact_path == learning_store.STYLE_LEARNING_APPROVAL_FACT
        and isinstance(previous_value, dict)
    ):
        try:
            with cal._connect() as conn:  # type: ignore[attr-defined]
                style_policy_apply = learning_distill.apply_approved_style_proposal(
                    conn,
                    env=body.env,
                    proposal=previous_value,
                    updated_by=f"approval:{body.decided_by}",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    value.update({"decision": decision, "decided_by": body.decided_by})
    if body.note:
        value["note"] = body.note
    if body.extra_facts:
        value.update(body.extra_facts)
    if style_policy_apply is not None:
        value["style_policy_apply"] = style_policy_apply
    try:
        cal.write_facts(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            namespace="approval",
            facts={fact_path: value},
            source=f"approval:{decision}",
            env=body.env,
        )
    except cal.FactNamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if gmail_draft is not None:
        event_id = cal.write_event(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            event_type="outbound_draft_created",
            goal="outreach",
            lane="commerce",
            actor=f"approval:{body.decided_by}",
            payload={"fact_path": fact_path, "gmail_draft": gmail_draft},
            env=body.env,
        )
        cal.write_facts(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            namespace="offer",
            facts={
                "offer.outreach_draft_created": True,
                "offer.gmail_draft_id": gmail_draft.get("draft_id"),
                "offer.gmail_thread_id": gmail_draft.get("thread_id"),
            },
            source="gmail:draft-created",
            source_event_id=event_id,
            env=body.env,
        )
        if linked_escalation_id is not None:
            handled_escalation_id = _mark_linked_reply_escalation_handled(
                escalation_id=linked_escalation_id,
                env=body.env,
                decided_by=body.decided_by,
            )
    derived_escalation_id = None
    learning_event_id: Optional[int] = None
    if decision == "rejected":
        learning_event_id = _record_draft_reject_learning(
            fact_path=fact_path,
            body=body,
            approval_value=value if isinstance(value, dict) else {},
            linked_escalation_id=linked_escalation_id,
        )
        correction = body.correction
        reject_tags_list = reject_tags.normalize_reject_tags(
            correction.tags if correction else None,
        )
        reject_note = (correction.note if correction and correction.note else body.note)
        suggested_fix = correction.suggested_fix if correction else None
        # An approval.reply_draft is always tied to an *open* escalation —
        # the operator rejecting the draft means "try again on the same
        # escalation". Opening a derived escalation here was creating an
        # unbounded chain (escalation → draft → rejected → escalation
        # → draft → ...). For reply-draft rejections we instead leave a
        # breadcrumb on the linked escalation; for any other approval
        # type we keep the legacy behaviour of opening a follow-up.
        if fact_path == "approval.reply_draft" and linked_escalation_id is not None:
            cal.note_rejected_draft(
                escalation_id=linked_escalation_id,
                fact_path=fact_path,
                note=reject_note,
                decided_by=body.decided_by,
                tags=reject_tags_list,
                suggested_fix=suggested_fix,
            )
        elif fact_path == learning_store.STYLE_LEARNING_APPROVAL_FACT:
            # Rejecting a batch style/strategy proposal must not open a KOL-facing
            # escalation — edit events stay unconsumed for the next distill batch.
            # Record structured negative feedback so the next distill prompt can
            # avoid repeating the rejected suggestion.
            try:
                cal.write_event(
                    identity_id=body.identity_id,
                    campaign_id=None,
                    event_type="style_proposal_rejected",
                    goal=None,
                    lane="meta",
                    actor=f"approval:{body.decided_by}",
                    payload={
                        "scope": value.get("scope") if isinstance(value, dict) else None,
                        "owner_user_id": (
                            value.get("owner_user_id") if isinstance(value, dict) else None
                        ),
                        "note": reject_note or "",
                        "tags": reject_tags_list,
                        "rejected_style_markdown": (
                            value.get("proposed_style_markdown")
                            if isinstance(value, dict) else None
                        ),
                        "rejected_strategy_markdown": (
                            value.get("proposed_strategy_markdown")
                            if isinstance(value, dict) else None
                        ),
                        "source_event_ids": (
                            value.get("source_event_ids") if isinstance(value, dict) else None
                        ),
                    },
                    env=body.env,
                )
            except Exception:
                log.warning(
                    "failed to record style_proposal_rejected feedback", exc_info=True,
                )
        else:
            derived_escalation_id = cal.open_escalation(
                identity_id=body.identity_id,
                campaign_id=body.campaign_id,
                reason=f"approval_rejected:{fact_path}",
                severity="normal",
                question_to_operator=(
                    f"Approval {fact_path} 已被驳回（{body.note or '无理由'}）。"
                    "请告诉 agent 应该如何回复 KOL。"
                ),
                env=body.env,
            )
    out: dict[str, Any] = {
        "ok": True,
        "decision": decision,
        "derived_escalation_id": derived_escalation_id,
        "linked_escalation_id": linked_escalation_id,
        "handled_escalation_id": handled_escalation_id,
        "learning_event_id": learning_event_id,
        "gmail_draft": gmail_draft,
    }
    if style_policy_apply is not None:
        out["style_policy_apply"] = style_policy_apply
    if outcome_policy_apply is not None:
        out["outcome_policy_apply"] = outcome_policy_apply
    return out


def _resolve_thread_id_from_events(
    *,
    identity_id: int,
    campaign_id: str | None,
    env: str,
    candidate_thread_id: str | None,
    source_message_id: str | None,
) -> str | None:
    """Verify (and if necessary, correct) the Gmail ``thread_id`` an
    upstream drafting skill placed on an ``approval.reply_draft``.

    Past incident: a drafting skill stored the inbound ``message_id``
    where Gmail expects a ``threadId``. Gmail's drafts.create then
    returns 404 ``Requested entity was not found``. To prevent that
    class of failure, we cross-check against ``kol_conversation_events``
    (which carries authoritative ``message_id`` and ``thread_id`` from
    the dispatcher). If the candidate matches a known message_id, swap
    it for the corresponding thread_id; if it already matches a known
    thread_id, leave it; otherwise return it unchanged (best-effort).
    """
    candidates = {c for c in (candidate_thread_id, source_message_id) if c}
    if not candidates:
        return candidate_thread_id
    try:
        events = cal.list_events(
            env=env, identity_id=identity_id,
            campaign_id=campaign_id, limit=200,
        )
    except Exception:  # noqa: BLE001 — defensive lookup, never fail the draft path
        return candidate_thread_id
    for ev in events:
        payload = ev.get("payload") if isinstance(ev, dict) else None
        if not isinstance(payload, dict):
            continue
        ev_thread = payload.get("thread_id")
        ev_msg = payload.get("message_id")
        if not isinstance(ev_thread, str) or not ev_thread:
            continue
        if candidate_thread_id and ev_thread == candidate_thread_id:
            return ev_thread
        if isinstance(ev_msg, str) and ev_msg and ev_msg in candidates:
            return ev_thread
    return candidate_thread_id


def _resolve_envelope_from_inbound(
    *,
    identity_id: int,
    campaign_id: str | None,
    env: str,
    source_message_id: str | None,
    thread_id: str | None,
) -> tuple[str | None, str | None]:
    """Recover (to, subject) for a reply draft from the inbound event.

    Child draft envelopes (e.g. kol-compensation-negotiator) intentionally
    return ``subject: null`` and omit ``to`` — the recipient is the inbound
    sender. The dispatcher should fill these in, but historically didn't,
    leaving the operator unable to approve. We re-derive them here from the
    ``kol_inbound_reply`` event whose payload carries ``from_addr`` and
    ``subject``. Matched by ``message_id``, falling back to ``thread_id``.
    Returns (None, None) when no matching event is found.
    """
    if not source_message_id and not thread_id:
        return None, None
    try:
        events = cal.list_events(
            env=env, identity_id=identity_id,
            campaign_id=campaign_id, limit=200,
        )
    except Exception:  # noqa: BLE001 — defensive lookup, never fail the draft path
        return None, None
    for ev in events:
        if ev.get("event_type") != "kol_inbound_reply":
            continue
        payload = ev.get("payload") if isinstance(ev, dict) else None
        if not isinstance(payload, dict):
            continue
        ev_msg = payload.get("message_id")
        ev_thread = payload.get("thread_id")
        matches = (
            (source_message_id and ev_msg == source_message_id)
            or (thread_id and ev_thread == thread_id)
        )
        if not matches:
            continue
        from_addr = str(payload.get("from_addr") or "").strip() or None
        in_subj = str(payload.get("subject") or "").strip()
        subject = None
        if in_subj:
            subject = in_subj if in_subj.lower().startswith("re:") else f"Re: {in_subj}"
        return from_addr, subject
    return None, None


def _gmail_self_emails(client: GmailClient) -> set[str]:
    """Authenticated account + ``KOL_OPS_GMAIL_REPLY_SELF`` extras."""
    self_emails: set[str] = set()
    profile = client.get_profile_email()
    if profile:
        self_emails.add(profile)
    extra_self = os.environ.get("KOL_OPS_GMAIL_REPLY_SELF", "")
    for part in extra_self.split(","):
        email = gmail_reply_envelope.extract_email(part.strip())
        if email:
            self_emails.add(email)
    return self_emails


def _fetch_inbound_for_reply_context(
    client: GmailClient,
    *,
    source_message_id: str | None,
    thread_id: str | None,
) -> Any | None:
    """Resolve the prior message used for reply-all Cc and Gmail quoting.

    Tries ``source_message_id`` first (inbound reply path). When that is
    missing or not a real Gmail id (e.g. proactive follow-up synthetic id),
    falls back to the last message in ``thread_id``.
    """
    if source_message_id:
        try:
            return client.get_message(source_message_id)
        except GmailUnavailable as exc:
            log.warning(
                "reply_draft inbound fetch failed msg=%s: %s",
                source_message_id,
                exc,
            )
    if thread_id:
        thread_msgs = client.get_thread(thread_id)
        if thread_msgs:
            last_id = str(thread_msgs[-1].get("id") or "").strip()
            if last_id:
                try:
                    return client.get_message(last_id)
                except GmailUnavailable as exc:
                    log.warning(
                        "reply_draft thread tail fetch failed thread=%s msg=%s: %s",
                        thread_id,
                        last_id,
                        exc,
                    )
    return None


def _apply_reply_all_cc_and_quote(
    *,
    body: str,
    to_addr: str,
    cc_addr: str | None,
    inbound: Any,
    client: GmailClient,
    html_body: bool,
) -> tuple[str, str | None]:
    """Apply reply-all Cc and Gmail-style quote when not already present."""
    if not cc_addr:
        cc_addr = gmail_reply_envelope.compute_reply_all_cc(
            inbound_from=inbound.from_addr,
            inbound_to=inbound.to,
            inbound_cc=inbound.cc,
            reply_to=to_addr,
            self_emails=_gmail_self_emails(client),
        ) or None
    if not gmail_reply_envelope.body_has_quoted_reply(body):
        body = gmail_reply_envelope.append_quoted_reply(
            body=body,
            quoted_from=inbound.from_addr,
            quoted_date=inbound.date,
            quoted_body=inbound.body,
            html=html_body,
        )
    return body, cc_addr


def _create_gmail_draft_for_reply_approval(
    *,
    identity_id: int,
    campaign_id: str | None,
    approval_value: dict[str, Any],
    env: str,
    client: Optional[GmailClient] = None,
) -> dict[str, Any]:
    draft = approval_value.get("draft")
    if not isinstance(draft, dict):
        raise HTTPException(status_code=400, detail="approval.reply_draft has no draft object")
    subject = str(draft.get("subject") or "").strip()
    body = str(draft.get("body") or "").strip()
    to_addr = str(draft.get("to") or "").strip()
    if not subject or not to_addr:
        # Child skill contracts allow subject=null and may omit `to` (the
        # recipient is the inbound sender). Recover from the inbound event
        # before failing the operator's approve click.
        anchor_thread_id, anchor_source_msg = reply_draft.extract_thread_anchors(
            approval_value,
        )
        recovered_to, recovered_subject = _resolve_envelope_from_inbound(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            source_message_id=anchor_source_msg,
            thread_id=anchor_thread_id,
        )
        if not to_addr and recovered_to:
            to_addr = recovered_to
        if not subject and recovered_subject:
            subject = recovered_subject
    missing = [
        name for name, val in (("subject", subject), ("body", body), ("to", to_addr))
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"approval.reply_draft draft missing required field(s): {', '.join(missing)}",
        )
    raw_thread_id, raw_source_msg = reply_draft.extract_thread_anchors(approval_value)
    resolved_thread_id = _resolve_thread_id_from_events(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        candidate_thread_id=raw_thread_id,
        source_message_id=raw_source_msg,
    )
    raw_attachments = draft.get("attachments") or []
    if not isinstance(raw_attachments, list):
        raise HTTPException(
            status_code=400,
            detail="approval.reply_draft draft.attachments must be a list of paths",
        )
    attachment_paths: list[str] = []
    for item in raw_attachments:
        path_str = str(item or "").strip()
        if not path_str:
            continue
        if not Path(path_str).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"draft attachment not found on disk: {path_str}",
            )
        attachment_paths.append(path_str)
    gmail = client or GmailClient()
    if not gmail.is_available():
        raise HTTPException(status_code=503, detail="gmail token or google_api.py unavailable")
    cc_addr = str(draft.get("cc") or "").strip() or None
    html_body = bool(draft.get("html"))
    inbound = _fetch_inbound_for_reply_context(
        gmail,
        source_message_id=raw_source_msg or None,
        thread_id=resolved_thread_id,
    )
    if inbound is not None:
        body, cc_addr = _apply_reply_all_cc_and_quote(
            body=body,
            to_addr=to_addr,
            cc_addr=cc_addr,
            inbound=inbound,
            client=gmail,
            html_body=html_body,
        )
    try:
        result = gmail.create_draft(
            to=to_addr,
            subject=subject,
            body=body,
            cc=cc_addr,
            html=html_body,
            thread_id=resolved_thread_id,
            attachments=attachment_paths or None,
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "draft_id": result.draft_id,
        "message_id": result.message_id,
        "thread_id": result.thread_id,
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
    }


@router.post("/approvals/{fact_path}/approve")
def approve(
    fact_path: str,
    body: ApprovalDecisionBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    return _approve_or_reject(fact_path=fact_path, decision="approved", body=body)


@router.post("/approvals/{fact_path}/reject")
def reject(
    fact_path: str,
    body: ApprovalDecisionBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    return _approve_or_reject(fact_path=fact_path, decision="rejected", body=body)


@router.post("/gmail/reconcile-sent")
def reconcile_sent(
    body: ReconcileSentBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    try:
        result = run_reconcile_all_mailboxes(
            env=body.env,
            lookback_days=body.lookback_days,
            max_results=body.max_results,
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/learning/backfill-edit-learning")
def backfill_edit_learning_route(
    body: BackfillEditLearningBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Backfill ``draft_edit_learning`` for sent drafts missed by lightweight reconcile."""
    _require_bridge_key(x_bridge_key)
    try:
        result = backfill_edit_learning_all_mailboxes(
            env=body.env,
            dry_run=body.dry_run,
            limit=body.limit,
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, **result}


_OPTIONAL_REPLY_LABELS = ("kol-outreach/pending-reply",)


def _modify_reply_idempotency_labels(
    client: GmailClient,
    message_id: str,
    *,
    add_names: list[str],
    remove_names: list[str],
) -> dict[str, Any]:
    """Apply reply idempotency labels; pending-reply is optional if missing."""
    return client.modify_labels(
        message_id,
        add_names=add_names,
        remove_names=remove_names,
        skip_missing_names=_OPTIONAL_REPLY_LABELS,
    )


@router.post("/gmail/mark-reply-handled")
def mark_reply_handled(
    body: MarkReplyHandledBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Apply handled label + remove pending-reply label on one message.

    Deterministic helper for dispatcher workflows that need idempotency
    label transitions without hand-rolling Gmail label logic in SKILL code.
    """
    _require_bridge_key(x_bridge_key)
    client = _gmail_client_for_inbound_labels(body)
    try:
        result = _modify_reply_idempotency_labels(
            client,
            body.message_id,
            add_names=[body.handled_label],
            remove_names=[body.pending_label],
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "env": body.env,
        "message_id": body.message_id,
        "added_label": body.handled_label,
        "removed_label": body.pending_label,
        "result": result,
    }


@router.post("/gmail/unmark-reply-handled")
def unmark_reply_handled(
    body: MarkReplyHandledBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Reverse ``mark-reply-handled`` so a reply can be re-dispatched.

    Removes the handled label and re-applies the pending-reply label.
    """
    _require_bridge_key(x_bridge_key)
    client = _gmail_client_for_inbound_labels(body)
    try:
        result = _modify_reply_idempotency_labels(
            client,
            body.message_id,
            add_names=[body.pending_label],
            remove_names=[body.handled_label],
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "env": body.env,
        "message_id": body.message_id,
        "added_label": body.pending_label,
        "removed_label": body.handled_label,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


@router.get("/escalations")
def list_escalations(
    state: Optional[str] = Query(default=None),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None, ge=1),
    campaign_id: Annotated[Optional[str], Depends(_campaign_id_query_optional_dep)] = None,
) -> dict[str, Any]:
    return {
        "escalations": cal.list_escalations(
            state=state,
            env=env,
            identity_id=identity_id,
            campaign_id=campaign_id,
        ),
    }


@router.get("/escalations/{escalation_id}")
def get_escalation(escalation_id: int) -> dict[str, Any]:
    row = cal.get_escalation(escalation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    return row


@router.post("/escalations")
def open_escalation(
    body: EscalationOpenBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    payload = body.model_dump(exclude_none=True)
    # Enrich resume_context with the authoritative Gmail thread_id when
    # only a source_message_id is supplied. This prevents downstream
    # drafting skills from mis-using the message_id as a thread_id (past
    # incident: Gmail drafts.create returned 404 because thread_id was
    # actually a message_id).
    ctx = payload.get("resume_context") or {}
    if (
        isinstance(ctx, dict)
        and ctx.get("source_message_id")
        and not ctx.get("thread_id")
    ):
        thread_id = _resolve_thread_id_from_events(
            identity_id=payload.get("identity_id") or 0,
            campaign_id=payload.get("campaign_id"),
            env=payload.get("env") or "LIVE",
            candidate_thread_id=None,
            source_message_id=str(ctx["source_message_id"]),
        )
        if thread_id:
            ctx["thread_id"] = thread_id
            payload["resume_context"] = ctx
    eid = cal.open_escalation(**payload)
    if eid is None:
        raise HTTPException(status_code=500, detail="open_escalation failed")
    return {"escalation_id": eid}


@router.patch("/escalations/{escalation_id}")
def resolve_escalation(
    escalation_id: int,
    body: EscalationResolveBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    try:
        cal.resolve_escalation(
            escalation_id=escalation_id,
            decision=body.decision,
            decided_by=body.decided_by,
            operator_answer=body.operator_answer,
            operator_facts=body.operator_facts,
            final_state=body.final_state,
        )
    except cal.EscalationStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Deterministic logic helpers (toolized skill steps)
#
# These endpoints expose pure decision logic that used to live inside the
# KOL skills as model-generated reasoning. They take structured input and
# return structured output with NO DB read/write, so they need no bridge key.
# Keeping them server-side lets both the skills and the Web console share one
# authoritative implementation (see pricing_engine / campaign_validation /
# dispatch_router / policies.match_escalation_rules).
# ---------------------------------------------------------------------------


class ComputeOfferBody(BaseModel):
    payload: dict[str, Any]


@router.post("/logic/compute-compensation-offer")
def compute_compensation_offer(body: ComputeOfferBody) -> dict[str, Any]:
    try:
        return pricing_engine.compute_offer(body.payload)
    except pricing_engine.PricingInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ValidateCampaignBody(BaseModel):
    campaign_id: str
    candidate: dict[str, Any]
    confirmed_high_budget: bool = False


@router.post("/logic/validate-campaign-config")
def validate_campaign_config_route(body: ValidateCampaignBody) -> dict[str, Any]:
    return campaign_validation.validate_campaign_config(
        body.candidate,
        campaign_id=body.campaign_id,
        confirmed_high_budget=body.confirmed_high_budget,
    )


class SelectNextSkillBody(BaseModel):
    goals: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/logic/select-next-skill")
def select_next_skill_route(body: SelectNextSkillBody) -> dict[str, Any]:
    return dispatch_router.select_next_skill(body.model_dump())


class SelectDraftablePlanBody(BaseModel):
    goals: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    lane_filter: Optional[str] = None


@router.post("/logic/select-draftable-plan")
def select_draftable_plan_route(body: SelectDraftablePlanBody) -> dict[str, Any]:
    return dispatch_router.select_draftable_plan(body.model_dump())


class MatchEscalationRulesBody(BaseModel):
    parsed: Optional[dict[str, Any]] = None
    signals: list[Any] = Field(default_factory=list)


class SanitizeClassifierFactsBody(BaseModel):
    namespaces: dict[str, dict[str, Any]] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/logic/sanitize-classifier-facts")
def sanitize_classifier_facts_route(
    body: SanitizeClassifierFactsBody,
) -> dict[str, Any]:
    """Preview classifier committed-key rewrites (no DB write)."""
    sanitized, adjustments = classifier_facts.sanitize_classifier_namespaces(
        body.namespaces, body.signals,
    )
    return {"namespaces": sanitized, "adjustments": adjustments}


@router.post("/logic/match-escalation-rules")
def match_escalation_rules_route(body: MatchEscalationRulesBody) -> dict[str, Any]:
    parsed = body.parsed
    if parsed is None:
        # Fetch + parse the active escalation_rules policy when the caller
        # did not supply one (saves the classifier a round-trip).
        with cal._connect() as conn:  # type: ignore[attr-defined]
            row = _policies.get_policy(conn, scope="escalation_rules")
        parsed = _policies.parse_escalation_rules((row or {}).get("content_md", ""))
    return _policies.match_escalation_rules(parsed, body.signals)


# ---------------------------------------------------------------------------
# Reply-draft persistence (toolized dispatcher Step 5.5)
# ---------------------------------------------------------------------------


class PersistReplyDraftBody(BaseModel):
    identity_id: int
    campaign_id: str
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    source_message_id: str
    primary_lane: str
    primary_goal: str
    child_skill: str
    child_envelope: dict[str, Any]
    latest_email: dict[str, Any]
    linked_escalation_id: Optional[int] = None
    contributing: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/reply-drafts/persist")
def persist_reply_draft(
    body: PersistReplyDraftBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Enrich + persist a reply draft as a CAL event + approval fact.

    Composes the two Step 5.5 writes the dispatcher used to hand-build:
    (1) ``kol_reply_draft_ready`` event, (2) ``approval.reply_draft`` fact.
    Envelope enrichment (``to`` / ``Re:`` subject / thread_id) is done first,
    so the approval fact never fails the Bridge's non-empty draft validator.
    """
    _require_bridge_key(x_bridge_key)
    if not cal.get_identity(body.identity_id):
        raise HTTPException(status_code=404, detail="identity not found")

    prior_row = cal.get_reply_draft_row(
        identity_id=body.identity_id,
        campaign_id=body.campaign_id,
        env=body.env,
    )
    prior_fact = prior_row.get("value") if isinstance(prior_row, dict) else None
    chase = cal.reply_chase_hint(
        identity_id=body.identity_id,
        campaign_id=body.campaign_id,
        message_id=body.source_message_id,
        thread_id=str(body.latest_email.get("thread_id") or "") or None,
        env=body.env,
    )
    superseded_prior_source: str | None = None
    if (
        chase.get("recommended_action") == "regenerate"
        and isinstance(prior_fact, dict)
    ):
        _, prior_src = reply_draft.extract_thread_anchors(prior_fact)
        if prior_src and prior_src != body.source_message_id:
            superseded_prior_source = prior_src

    try:
        merged = reply_draft.enrich_envelope(body.child_envelope, body.latest_email)
    except reply_draft.ReplyDraftError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    child_skill = (body.child_skill or "").strip()
    contributing_skills = list(body.contributing) if body.contributing else []
    if not child_skill:
        if len(contributing_skills) > 1:
            child_skill = "kol-reply-synthesizer"
        elif contributing_skills:
            child_skill = str(contributing_skills[0].get("skill") or "").strip()
    if not child_skill:
        raise HTTPException(
            status_code=400,
            detail="child_skill is required (or pass contributing[] with skill names)",
        )
    if not contributing_skills:
        contributing_skills = [
            {
                "lane": body.primary_lane,
                "goal": body.primary_goal,
                "skill": child_skill,
            },
        ]

    event_payload = reply_draft.build_draft_event_payload(
        source_message_id=body.source_message_id,
        primary_lane=body.primary_lane,
        primary_goal=body.primary_goal,
        child_skill=child_skill,
        merged_draft=merged,
        contributing_skills=contributing_skills,
    )
    event_id = cal.write_event(
        identity_id=body.identity_id,
        event_type="kol_reply_draft_ready",
        actor="agent:kol-reply-dispatcher",
        campaign_id=body.campaign_id,
        lane=body.primary_lane,
        goal=body.primary_goal,
        payload=event_payload,
        env=body.env,
    )
    fact_value = reply_draft.build_approval_fact_value(
        source_message_id=body.source_message_id,
        primary_lane=body.primary_lane,
        primary_goal=body.primary_goal,
        child_skill=child_skill,
        merged_draft=merged,
        linked_escalation_id=body.linked_escalation_id,
        contributing_skills=contributing_skills,
    )
    if superseded_prior_source:
        fact_value["chase_supersede"] = {
            "prior_source_message_id": superseded_prior_source,
            "superseded_for_follow_up": True,
        }
    orphan_discard: dict[str, Any] | None = None
    if superseded_prior_source and isinstance(prior_fact, dict):
        orphan_discard = orphan_gmail_draft.discard_orphan_gmail_draft(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            env=body.env,
            prior_fact=prior_fact,
        )
        if isinstance(fact_value.get("chase_supersede"), dict) and orphan_discard:
            fact_value["chase_supersede"]["orphan_gmail_discard"] = orphan_discard
    try:
        written = cal.write_facts_multi(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            namespaces={"approval": {"approval.reply_draft": fact_value}},
            source=f"draft:{body.source_message_id}",
            source_event_id=event_id,
            env=body.env,
        )
    except cal.FactNamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if superseded_prior_source:
        cal.write_event(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            event_type="kol_reply_draft_superseded",
            goal=body.primary_goal,
            lane=body.primary_lane,
            actor="agent:kol-reply-dispatcher",
            payload={
                "old_source_message_id": superseded_prior_source,
                "new_source_message_id": body.source_message_id,
                "draft_event_id": event_id,
                "orphan_gmail_discard": orphan_discard,
            },
            env=body.env,
        )
    return {
        "ok": True,
        "draft_event_id": event_id,
        "written": written,
        "draft": merged,
        "child_skill": child_skill,
        "contributing_skills": contributing_skills,
        "chase_superseded": superseded_prior_source is not None,
        "prior_source_message_id": superseded_prior_source,
        "orphan_gmail_discard": orphan_discard,
    }


# ---------------------------------------------------------------------------
# Learning exports (read-only + policy publish)
# ---------------------------------------------------------------------------


@router.get("/learning/fact-corrections")
def get_fact_corrections(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """Return manual fact writes that superseded classifier email writes."""
    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = learning_store.list_fact_corrections(
            conn,
            env=env,
            identity_id=identity_id,
            campaign_id=campaign_id,
            limit=limit,
        )
    return {"env": env, "count": len(rows), "corrections": rows}


@router.get("/learning/negotiation-history")
def get_negotiation_history(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    campaign_id: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    """Aggregate compensation facts for offline pricing calibration."""
    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = learning_store.list_negotiation_history(
            conn, env=env, campaign_id=campaign_id, limit=limit,
        )
    return {"env": env, "count": len(rows), "records": rows}


@router.get("/learning/reject-events")
def get_reject_events(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    goal: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Return structured draft-rejection learning events."""
    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = learning_store.list_learning_events(
            conn,
            env=env,
            event_types=("draft_rejected_learning",),
            identity_id=identity_id,
            campaign_id=campaign_id,
            goal=goal,
            limit=limit,
        )
    return {"env": env, "count": len(rows), "events": rows}


@router.get("/learning/edit-events")
def get_edit_events(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Return Gmail sent-body edit learning events."""
    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = learning_store.list_learning_events(
            conn,
            env=env,
            event_types=("draft_edit_learning",),
            identity_id=identity_id,
            campaign_id=campaign_id,
            limit=limit,
        )
    return {"env": env, "count": len(rows), "events": rows}


@router.get("/learning/edit-distance-trend")
def get_edit_distance_trend(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    days: int = Query(default=90, ge=1, le=730),
    bucket: str = Query(default="week", pattern="^(day|week)$"),
    goal: Optional[str] = Query(default=None),
    child_skill: Optional[str] = Query(default=None),
    operator_user_id: Optional[int] = Query(default=None),
) -> dict[str, Any]:
    """Convergence metric: edit_distance trend over time (read-only).

    Lower ``avg_edit_distance`` / ``was_edited_rate`` over time means the
    operator is editing AI drafts less, i.e. learning is converging on their
    style/strategy.
    """
    with cal._connect() as conn:  # type: ignore[attr-defined]
        return learning_store.edit_distance_trend(
            conn,
            env=env,
            days=days,
            bucket=bucket,
            goal=goal,
            child_skill=child_skill,
            operator_user_id=operator_user_id,
        )


class LearningApplyBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    updated_by: str = Field(default="bridge:learning-apply", min_length=1, max_length=120)
    limit: int = Field(default=200, ge=1, le=500)


class EditPolicyApplyBody(LearningApplyBody):
    scope: str = Field(default="company_style", pattern="^(company_style|user_style)$")
    owner_user_id: Optional[int] = None


class PricingCampaignApplyBody(BaseModel):
    env: str = Field(default="TEST", pattern="^(TEST|LIVE)$")
    campaign_id: str = Field(min_length=1, max_length=120)
    paid_ratio_override: Optional[float] = Field(default=None, ge=0, le=1)


@router.post("/learning/apply-reject-policy")
def apply_reject_learning_policy(
    body: LearningApplyBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        result = learning_distill.apply_reject_policy(
            conn,
            env=body.env,
            updated_by=body.updated_by,
            limit=body.limit,
        )
    return {"ok": True, "env": body.env, **result}


@router.post("/learning/apply-edit-policy")
def apply_edit_learning_policy(
    body: EditPolicyApplyBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    try:
        with cal._connect() as conn:  # type: ignore[attr-defined]
            result = learning_distill.apply_edit_policy(
                conn,
                env=body.env,
                scope=body.scope,
                updated_by=body.updated_by,
                owner_user_id=body.owner_user_id,
                limit=body.limit,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "env": body.env, "scope": body.scope, **result}


@router.post("/learning/apply-pricing-calibration-policy")
def apply_pricing_calibration_policy_route(
    body: LearningApplyBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    env = learning_jobs.require_scheduled_learning_env(body.env)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        result = learning_distill.apply_pricing_calibration_policy(
            conn,
            env=env,
            updated_by=body.updated_by,
            limit=body.limit,
        )
    return {"ok": True, "env": env, **result}


@router.post("/learning/apply-pricing-campaign")
def apply_pricing_campaign_override(
    body: PricingCampaignApplyBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    cfg = cal.get_campaign_config(body.campaign_id, env=body.env)
    if not cfg:
        raise HTTPException(status_code=404, detail="campaign not found")
    ratio = body.paid_ratio_override
    if ratio is None:
        with cal._connect() as conn:  # type: ignore[attr-defined]
            records = learning_store.list_negotiation_history(
                conn, env=body.env, campaign_id=body.campaign_id,
            )
        counters: list[float] = []
        for rec in records:
            facts = rec.get("facts") or {}
            counter = facts.get("offer.latest_counter_amount")
            req = facts.get("offer.latest_requested_amount")
            try:
                if counter is not None and req is not None and float(req) > 0:
                    counters.append(float(counter) / float(req))
            except (TypeError, ValueError):
                continue
        ratio = round(sum(counters) / len(counters), 3) if counters else 0.55
    cal.upsert_campaign_config(
        campaign_id=body.campaign_id,
        env=body.env,
        paid_ratio_override=ratio,
    )
    return {
        "ok": True,
        "campaign_id": body.campaign_id,
        "env": body.env,
        "paid_ratio_override": ratio,
    }


class RunScheduledLearningJobsBody(BaseModel):
    env: str = Field(
        default="LIVE",
        pattern="^LIVE$",
        description="Autonomous learning is LIVE-only (production data).",
    )
    suite: Optional[str] = Field(
        default=None,
        description="capture | distill | pricing | audit | nightly | all",
    )
    jobs: Optional[list[str]] = Field(default=None, description="Explicit job names override suite")
    triggered_by: str = Field(default="cron:learning", min_length=1, max_length=120)
    limit: int = Field(default=200, ge=1, le=500)
    lookback_days: int = Field(default=7, ge=1, le=30)
    max_results: int = Field(default=100, ge=1, le=500)
    min_pricing_samples: int = Field(default=3, ge=1, le=50)
    dry_run: bool = False


@router.post("/learning/run-scheduled-jobs")
def run_scheduled_learning_jobs(
    body: RunScheduledLearningJobsBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Run the autonomous learning job suite on LIVE (cron entrypoint)."""
    _require_bridge_key(x_bridge_key)
    try:
        return learning_jobs.run_scheduled_jobs(
            env=body.env,
            triggered_by=body.triggered_by,
            jobs=body.jobs,
            suite=body.suite,
            limit=body.limit,
            lookback_days=body.lookback_days,
            max_results=body.max_results,
            min_pricing_samples=body.min_pricing_samples,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class PromoteStrategyBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    goal: str = Field(min_length=1, max_length=120)
    min_approvals: int = Field(default=learning_promote.DEFAULT_MIN_APPROVALS, ge=1, le=50)
    min_age_days: int = Field(default=learning_promote.DEFAULT_MIN_AGE_DAYS, ge=0, le=365)
    dry_run: bool = True
    triggered_by: str = Field(
        default="bridge:promote-strategy", min_length=1, max_length=120,
    )


@router.get("/learning/overview")
def get_learning_overview(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    runs_limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Dashboard snapshot: edit batch progress, pending proposals, job runs."""
    with cal._connect() as conn:  # type: ignore[attr-defined]
        return learning_overview.build_learning_overview(
            conn, env=env, runs_limit=runs_limit,
        )


@router.post("/learning/promote-strategy")
def promote_strategy_route(
    body: PromoteStrategyBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Promote a stabilized reply_strategy goal section into its skill ref.

    Default ``dry_run=true`` returns the proposed markdown + target path. With
    ``dry_run=false`` it writes ``references/learned/<goal>.md`` (audited) — the
    caller must run ``sync skills`` afterward to push to kol-orchestrator.
    """
    _require_bridge_key(x_bridge_key)
    try:
        with cal._connect() as conn:  # type: ignore[attr-defined]
            return learning_promote.promote_strategy_to_skill(
                conn,
                env=body.env,
                goal=body.goal,
                min_approvals=body.min_approvals,
                min_age_days=body.min_age_days,
                dry_run=body.dry_run,
                triggered_by=body.triggered_by,
            )
    except learning_promote.PromoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/learning/job-runs")
def list_learning_job_runs(
    env: Optional[str] = Query(default=None, pattern="^(TEST|LIVE)$"),
    job_name: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, pattern="^(ok|skipped|error|running)$"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Audit trail for scheduled learning jobs (newest first)."""
    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = learning_job_store.list_runs(
            conn,
            env=env,
            job_name=job_name,
            status=status,
            limit=limit,
        )
    return {"count": len(rows), "runs": rows}


# ---------------------------------------------------------------------------
# Policy documents (Phase E)
# ---------------------------------------------------------------------------


_POLICY_SCOPES = {
    "company_style",
    "user_style",
    "escalation_rules",
    "reply_learning",
    "reply_strategy",
    "pricing_calibration",
    "outcome_strategy",
}


def _resolve_owner(scope: str, owner_user_id: Optional[int]) -> Optional[int]:
    if scope == "user_style":
        if owner_user_id is None:
            raise HTTPException(status_code=400, detail="user_style requires owner_user_id")
        return int(owner_user_id)
    if owner_user_id is not None:
        raise HTTPException(status_code=400, detail=f"{scope} must omit owner_user_id")
    return None


@router.get("/policies/{scope}")
def get_policy(
    scope: str,
    owner_user_id: Optional[int] = Query(default=None),
    env: Optional[str] = Query(default=None, pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if scope not in _POLICY_SCOPES:
        raise HTTPException(status_code=404, detail="unknown scope")
    owner = _resolve_owner(scope, owner_user_id)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = _policies.get_policy(
            conn, scope=scope, owner_user_id=owner, env=env,
        )
    return {"policy": row}


@router.put("/policies/{scope}")
def put_policy(
    scope: str,
    body: PolicyPutBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    if scope not in _POLICY_SCOPES:
        raise HTTPException(status_code=404, detail="unknown scope")
    owner = _resolve_owner(scope, body.owner_user_id)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = _policies.put_policy(
            conn,
            scope=scope,
            content_md=body.content_md,
            updated_by=body.updated_by,
            owner_user_id=owner,
            title=body.title,
        )
    return {"policy": row}


@router.get("/policies/{scope}/history")
def list_policy_history(
    scope: str,
    owner_user_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    if scope not in _POLICY_SCOPES:
        raise HTTPException(status_code=404, detail="unknown scope")
    owner = _resolve_owner(scope, owner_user_id)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = _policies.list_policy_history(
            conn, scope=scope, owner_user_id=owner, limit=limit
        )
    return {"history": rows}


@router.post("/policies/{scope}/rollback")
def rollback_policy_route(
    scope: str,
    body: PolicyRollbackBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    """Roll a policy back to a prior version (forward-write, audited)."""
    _require_bridge_key(x_bridge_key)
    if scope not in _POLICY_SCOPES:
        raise HTTPException(status_code=404, detail="unknown scope")
    owner = _resolve_owner(scope, body.owner_user_id)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        try:
            row = _policies.rollback_policy(
                conn,
                scope=scope,
                to_version=body.to_version,
                updated_by=body.updated_by,
                owner_user_id=owner,
                env=body.env,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy": row, "rolled_back_to": body.to_version}


@router.get("/policies/escalation_rules/parsed")
def get_parsed_escalation_rules() -> dict[str, Any]:
    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = _policies.get_policy(conn, scope="escalation_rules")
    if not row:
        return {"top": {}, "rules": [], "version": 0}
    parsed = _policies.parse_escalation_rules(row["content_md"])
    parsed["version"] = row["version"]
    return parsed
