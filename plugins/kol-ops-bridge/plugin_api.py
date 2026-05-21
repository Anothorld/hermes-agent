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


@router.get("/campaigns/{campaign_id}/lanes")
def get_lanes(
    campaign_id: str,
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Return per-identity lane snapshots for the entire campaign.

    Output: ``{ "items": [ { "identity_id":..., "lanes":{commerce:[...], ...} }, ... ] }``.
    Suitable for the Web kanban lane filter.
    """
    candidates = cal.list_candidates(campaign_id, env=env)
    items = []
    for c in candidates:
        if not c.get("identity_id"):
            continue
        items.append({
            "identity_id": c["identity_id"],
            "candidate_status": c["candidate_status"],
            "relationship_status": c["relationship_status"],
            "lanes": cal.get_lanes_view(
                identity_id=c["identity_id"],
                campaign_id=campaign_id, env=env,
            ),
        })
    return {"items": items}


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
