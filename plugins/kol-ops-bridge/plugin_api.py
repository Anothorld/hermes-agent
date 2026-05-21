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
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import cal
from . import discovery_router
from . import policies as _policies
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
# Pydantic bodies
# ---------------------------------------------------------------------------


class IdentityUpsertBody(BaseModel):
    primary_handle: str
    platform: str = "instagram"
    primary_email: Optional[str] = None
    display_name: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None
    contact_role: str = "kol"
    default_shipping_address: Optional[dict[str, Any]] = None
    default_payment_method: Optional[str] = None
    notes: Optional[str] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class CampaignConfigUpsertBody(BaseModel):
    label: Optional[str] = None
    product_unit_price: Optional[float] = None
    barter_policy: Optional[str] = None
    paid_ceiling: Optional[float] = None
    commission_band: Optional[dict[str, Any]] = None
    deliverable_platforms: Optional[list[str]] = None
    deliverable_count_per_platform: Optional[int] = None
    extra_notes: Optional[str] = None
    brief_template_id: Optional[str] = None
    sku_whitelist: Optional[list[str]] = None
    color_variant_policy: Optional[str] = None
    audit_standards_md: Optional[str] = None
    followup_intervals: Optional[dict[str, Any]] = None
    contract_required: Optional[bool] = None
    status: Optional[str] = None
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


class FactsWriteBody(BaseModel):
    campaign_id: Optional[str] = None
    namespace: str
    facts: dict[str, Any]
    source: str = "manual"
    source_event_id: Optional[int] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class EscalationOpenBody(BaseModel):
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


class ApprovalDecisionBody(BaseModel):
    identity_id: int
    campaign_id: Optional[str] = None
    decided_by: str
    note: Optional[str] = None
    extra_facts: Optional[dict[str, Any]] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class ArchiveBody(BaseModel):
    campaign_id: str
    outcome: str
    preferred_skus: Optional[list[str]] = None
    preferred_mode: Optional[str] = None
    avg_revision_rounds: Optional[float] = None
    delivery_quality: Optional[float] = None
    decided_by: str = "skill:archival-writer"


class RouteDiscoveryBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    selected_by: str = "agent"
    operator_note: str = ""


class FactsWriteMultiBody(BaseModel):
    campaign_id: Optional[str] = None
    namespaces: dict[str, dict[str, Any]]
    source: str = "skill"
    source_event_id: Optional[int] = None
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")


class PolicyPutBody(BaseModel):
    content_md: str
    updated_by: str
    owner_user_id: Optional[int] = None
    title: Optional[str] = None


class EventWriteBody(BaseModel):
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
    return cal.get_relationship(identity_id) or {"identity_id": identity_id, "total_collabs": 0}


@router.get("/identities/{identity_id}/relationship/reusable-facts")
def get_reusable_facts(identity_id: int) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return cal.get_reusable_facts(identity_id)


@router.get("/identities/{identity_id}/goals")
def get_goal_state(
    identity_id: int,
    campaign_id: str = Query(...),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {"goals": cal.get_goal_state(identity_id=identity_id,
                                        campaign_id=campaign_id, env=env)}


@router.get("/identities/{identity_id}/timeline")
def get_identity_timeline(
    identity_id: int,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    campaign_id: Optional[str] = Query(default=None),
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


@router.get("/events/recent")
def get_recent_events(
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    campaign_id: Optional[str] = Query(default=None),
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
        decided_by=body.decided_by,
    )
    cal.recompute_goals(identity_id=identity_id, campaign_id=body.campaign_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Campaigns + candidates
# ---------------------------------------------------------------------------


@router.put("/campaigns/{campaign_id}")
def upsert_campaign_config(
    campaign_id: str,
    body: CampaignConfigUpsertBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    cal.upsert_campaign_config(campaign_id=campaign_id,
                               **body.model_dump(exclude_none=True))
    return {"ok": True, "campaign_id": campaign_id}


@router.get("/campaigns/{campaign_id}")
def get_campaign_config(campaign_id: str) -> dict[str, Any]:
    cfg = cal.get_campaign_config(campaign_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="campaign not found")
    return cfg


@router.get("/campaigns/{campaign_id}/candidates")
def list_candidates(
    campaign_id: str,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    return {"candidates": cal.list_candidates(campaign_id, env=env)}


@router.post("/campaigns/{campaign_id}/candidates")
def upsert_candidate(
    campaign_id: str,
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
    campaign_id: str,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    n = cal.resolve_candidate_relationships(campaign_id=campaign_id, env=env)
    return {"resolved": n}


@router.post("/campaigns/{campaign_id}/candidates/select")
def select_candidates(
    campaign_id: str,
    body: CandidateSelectBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    n = cal.select_candidates_for_outreach(
        campaign_id=campaign_id, identity_ids=body.identity_ids,
        selected_by=body.selected_by, env=body.env,
    )
    return {"selected": n}


@router.post("/campaigns/{campaign_id}/candidates/route-discovery")
def route_discovery(
    campaign_id: str,
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


@router.get("/campaigns/{campaign_id}/lanes")
def get_lanes(
    campaign_id: str,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Return per-identity lane snapshots for the entire campaign.

    Output: ``{ "items": [ { "identity_id":..., "handle":...,
    "candidate_status":..., "relationship_status":...,
    "repeat_count":..., "last_outcome":..., "archived": bool,
    "lanes":{commerce:[...], ...} }, ... ],
    "counts": {"pending_approvals": N, "open_escalations": M} }``.
    Suitable for the Web kanban lane filter + top-of-page badges.
    """
    candidates = cal.list_candidates(campaign_id, env=env)
    items = []
    for c in candidates:
        if not c.get("identity_id"):
            continue
        ident = cal.get_identity(c["identity_id"]) or {}
        rel = cal.get_relationship(c["identity_id"]) or {}
        items.append({
            "identity_id": c["identity_id"],
            "handle": ident.get("primary_handle") or f"id{c['identity_id']}",
            "candidate_status": c["candidate_status"],
            "relationship_status": c["relationship_status"],
            "repeat_count": int(rel.get("total_collabs") or 0),
            "last_outcome": rel.get("last_outcome"),
            "archived": c["candidate_status"] in ("archived", "rejected"),
            "lanes": cal.get_lanes_view(
                identity_id=c["identity_id"],
                campaign_id=campaign_id, env=env,
            ),
        })
    counts = {
        "pending_approvals": sum(
            1 for a in cal.list_pending_approvals(env=env)
            if a.get("campaign_id") == campaign_id
        ),
        "open_escalations": sum(
            1 for e in cal.list_escalations(state="awaiting_answer", env=env)
            if e.get("campaign_id") == campaign_id
        ),
    }
    return {"items": items, "counts": counts}


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
    try:
        n = cal.write_facts(
            identity_id=identity_id,
            campaign_id=body.campaign_id,
            namespace=body.namespace,
            facts=body.facts,
            source=body.source,
            source_event_id=body.source_event_id,
            env=body.env,
        )
    except cal.FactNamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"written": n}


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
    try:
        written = cal.write_facts_multi(
            identity_id=identity_id,
            campaign_id=body.campaign_id,
            namespaces=body.namespaces,
            source=body.source,
            source_event_id=body.source_event_id,
            env=body.env,
        )
    except cal.FactNamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"written": written}


@router.get("/identities/{identity_id}/dispatch-context")
def get_dispatch_context(
    identity_id: int,
    campaign_id: str = Query(...),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Bundle the read snapshots ``kol-reply-dispatcher`` needs in one call.

    Returns ``{goals, lanes, relationship, reusable_facts, campaign_config}``
    for a single (identity, campaign) pair. Replaces 5 separate reads
    with 1. ``campaign_config`` is ``None`` if the campaign row is
    missing (caller must surface that as a routing error).
    """
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
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
        "reusable_facts": cal.get_reusable_facts(identity_id),
        "campaign_config": cal.get_campaign_config(campaign_id),
    }


@router.get("/facts/{identity_id}")
def read_facts(
    identity_id: int,
    campaign_id: Optional[str] = Query(default=None),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if not cal.get_identity(identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    return {"facts": cal.latest_facts_for(
        identity_id=identity_id, campaign_id=campaign_id, env=env,
    )}


# ---------------------------------------------------------------------------
# Approvals (cross-cutting view of approval.* facts)
# ---------------------------------------------------------------------------


@router.get("/approvals")
def list_approvals(
    status: str = Query(default="pending", pattern="^(pending|all)$"),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if status == "pending":
        return {"approvals": cal.list_pending_approvals(env=env)}
    raise HTTPException(status_code=501, detail="status=all not implemented yet")


def _approve_or_reject(
    *, fact_path: str, decision: str, body: ApprovalDecisionBody
) -> dict[str, Any]:
    if not fact_path.startswith("approval."):
        raise HTTPException(status_code=400, detail="fact_path must start with 'approval.'")
    if not cal.get_identity(body.identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    value: dict[str, Any] = {"decision": decision, "decided_by": body.decided_by}
    if body.note:
        value["note"] = body.note
    if body.extra_facts:
        value.update(body.extra_facts)
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
    derived_escalation_id = None
    if decision == "rejected":
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
    return {"ok": True, "decision": decision,
            "derived_escalation_id": derived_escalation_id}


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


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


@router.get("/escalations")
def list_escalations(
    state: Optional[str] = Query(default=None),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    return {"escalations": cal.list_escalations(state=state, env=env)}


@router.post("/escalations")
def open_escalation(
    body: EscalationOpenBody,
    x_bridge_key: Optional[str] = Header(default=None, alias="X-Bridge-Key"),
) -> dict[str, Any]:
    _require_bridge_key(x_bridge_key)
    eid = cal.open_escalation(**body.model_dump(exclude_none=True))
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
    if body.final_state not in {"resolved", "re_escalated", "aborted", "answered"}:
        raise HTTPException(status_code=400, detail="invalid final_state")
    cal.resolve_escalation(
        escalation_id=escalation_id,
        decision=body.decision,
        decided_by=body.decided_by,
        operator_answer=body.operator_answer,
        operator_facts=body.operator_facts,
        final_state=body.final_state,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Policy documents (Phase E)
# ---------------------------------------------------------------------------


_POLICY_SCOPES = {"company_style", "user_style", "escalation_rules"}


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
) -> dict[str, Any]:
    if scope not in _POLICY_SCOPES:
        raise HTTPException(status_code=404, detail="unknown scope")
    owner = _resolve_owner(scope, owner_user_id)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = _policies.get_policy(conn, scope=scope, owner_user_id=owner)
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


@router.get("/policies/escalation_rules/parsed")
def get_parsed_escalation_rules() -> dict[str, Any]:
    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = _policies.get_policy(conn, scope="escalation_rules")
    if not row:
        return {"top": {}, "rules": [], "version": 0}
    parsed = _policies.parse_escalation_rules(row["content_md"])
    parsed["version"] = row["version"]
    return parsed
