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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import cal
from . import discovery_router
from . import policies as _policies
from .gmail_client import GmailClient, GmailUnavailable
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
    product_display_name: Optional[str] = None
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
    test_mode_to: Optional[str] = None
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


class CandidateStatusBody(BaseModel):
    identity_ids: list[int]
    candidate_status: str = Field(pattern="^(discovered|shortlisted|selected_for_outreach|needs_review|rejected|archived)$")
    review_reason: Optional[str] = None
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


class ReconcileSentBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    lookback_days: int = Field(default=7, ge=1, le=90)
    max_results: int = Field(default=100, ge=1, le=500)


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
    campaign_id: str,
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
    if (h := m(r"\b(?:paid[\s_-]*ceiling|预算上限|上限|cap)\s*[:：]?\s*\$?(\d+(?:\.\d+)?)")):
        out["paid_ceiling"] = float(h.group(1))
    elif (h := m(r"\b(?:预算|budget)\s*[:：]?\s*\$?(\d+(?:\.\d+)?)")):
        out["paid_ceiling"] = float(h.group(1))

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
    campaign_id: str,
    env: Optional[str] = Query(default=None, pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    cfg = cal.get_campaign_config(campaign_id, env=env)
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


@router.post("/campaigns/{campaign_id}/candidates/status")
def set_candidate_status(
    campaign_id: str,
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
        # Pull a handful of fact values the Web kanban renders on the
        # card itself (sent-time chip, interest-signal badge) so the FE
        # doesn't have to fan out N extra /facts requests.
        facts = cal.latest_facts_for(
            identity_id=c["identity_id"],
            campaign_id=campaign_id, env=env,
        )
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
    try:
        written = cal.write_facts_multi(
            identity_id=identity_id,
            campaign_id=campaign_id,
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
        # Same shape as GET /relationship/reusable-facts:
        # ``{"identity_id":..., "facts":{...}}``.
        "reusable_facts": {
            "identity_id": identity_id,
            "facts": cal.get_reusable_facts(identity_id),
        },
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
    status: str = Query(
        default="pending",
        pattern="^(pending|approved|rejected|all)$",
    ),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if status == "pending":
        return {"approvals": cal.list_pending_approvals(env=env)}
    return {"approvals": cal.list_decided_approvals(status=status, env=env)}


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


def _approve_or_reject(
    *, fact_path: str, decision: str, body: ApprovalDecisionBody
) -> dict[str, Any]:
    if not fact_path.startswith("approval."):
        raise HTTPException(status_code=400, detail="fact_path must start with 'approval.'")
    if not cal.get_identity(body.identity_id):
        raise HTTPException(status_code=404, detail="identity not found")
    previous_value = None
    if body.campaign_id:
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
    gmail_draft: dict[str, Any] | None = None
    if decision == "approved" and fact_path == "approval.reply_draft":
        gmail_draft = _create_gmail_draft_for_reply_approval(
            identity_id=body.identity_id,
            campaign_id=body.campaign_id,
            approval_value=value,
            env=body.env,
        )
        value["gmail_draft"] = gmail_draft
    value.update({"decision": decision, "decided_by": body.decided_by})
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
    if decision == "rejected":
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
                note=body.note,
                decided_by=body.decided_by,
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
    return {"ok": True, "decision": decision,
            "derived_escalation_id": derived_escalation_id,
            "linked_escalation_id": linked_escalation_id,
            "handled_escalation_id": handled_escalation_id,
            "gmail_draft": gmail_draft}


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


def _create_gmail_draft_for_reply_approval(
    *,
    identity_id: int,
    campaign_id: str | None,
    approval_value: dict[str, Any],
    env: str,
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
        src_msg = str(approval_value.get("source_message_id") or "") or None
        thr = str(draft.get("thread_id") or "") or None
        recovered_to, recovered_subject = _resolve_envelope_from_inbound(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            source_message_id=src_msg,
            thread_id=thr,
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
    raw_thread_id = str(draft.get("thread_id") or "") or None
    raw_source_msg = str(approval_value.get("source_message_id") or "") or None
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
    client = GmailClient()
    if not client.is_available():
        raise HTTPException(status_code=503, detail="gmail token or google_api.py unavailable")
    try:
        result = client.create_draft(
            to=to_addr,
            subject=subject,
            body=body,
            cc=str(draft.get("cc") or "") or None,
            html=bool(draft.get("html")),
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
    client = GmailClient()
    if not client.is_available():
        raise HTTPException(status_code=503, detail="gmail token or google_api.py unavailable")
    try:
        sent_thread_ids = client.list_sent_thread_ids(
            lookback_days=body.lookback_days,
            max_results=body.max_results,
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reconciled: list[dict[str, Any]] = []
    for row in cal.list_approved_reply_drafts(env=body.env):
        gmail_draft = row.get("gmail_draft") if isinstance(row, dict) else {}
        thread_id = gmail_draft.get("thread_id") if isinstance(gmail_draft, dict) else None
        if not thread_id or thread_id not in sent_thread_ids:
            continue
        identity_id = int(row["identity_id"])
        campaign_id = row.get("campaign_id")
        event_id = cal.write_event(
            identity_id=identity_id,
            campaign_id=campaign_id,
            event_type="outbound_sent",
            goal="outreach",
            lane="commerce",
            actor="gmail:sent-reconcile",
            payload={"thread_id": thread_id, "gmail_draft": gmail_draft},
            env=body.env,
        )
        cal.write_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespace="offer",
            facts={
                "offer.outreach_sent": True,
                "offer.outreach_sent_at": now,
                "offer.gmail_sent_thread_id": thread_id,
            },
            source="gmail:sent-reconcile",
            source_event_id=event_id,
            env=body.env,
        )
        reconciled.append({
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "thread_id": thread_id,
            "event_id": event_id,
        })
    return {
        "ok": True,
        "env": body.env,
        "sent_threads_seen": len(sent_thread_ids),
        "reconciled_count": len(reconciled),
        "reconciled": reconciled,
    }


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
