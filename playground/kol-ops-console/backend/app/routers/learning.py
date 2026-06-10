"""Proxy Bridge learning endpoints for the Console UI."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, require_role
from ..discovery_feedback import (
    list_failed_shortlist_captures,
    replay_shortlist_capture,
)
from ..learning_job_store import create_job, get_job, run_in_background

router = APIRouter(prefix="/learning", tags=["learning"])


def _learning_bridge_timeout(bridge: BridgeClient) -> float:
    """Long-running LLM distill + cron suites exceed default 60s bridge timeout."""
    return getattr(bridge, "_learning_timeout", 300.0)

PROMOTABLE_GOALS = (
    "interest_qualification",
    "product_selection",
    "deliverables_scope",
    "compensation_negotiation",
)

LEARNING_POLICY_SCOPES = frozenset({
    "reply_learning",
    "reply_strategy",
    "company_style",
    "user_style",
    "pricing_calibration",
    "outcome_strategy",
})

# Dynamic learned-discovery-criteria scopes (per SPU / per category).
DISCOVERY_CRITERIA_PREFIX = "discovery_criteria:"


def _scope_allowed(scope: str) -> bool:
    return scope in LEARNING_POLICY_SCOPES or scope.startswith(DISCOVERY_CRITERIA_PREFIX)


@router.get("/overview")
async def get_learning_overview(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    runs_limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return await bridge._req(
            "GET", "/learning/overview",
            params={"env": env, "runs_limit": runs_limit},
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/job-runs")
async def list_job_runs(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: Optional[str] = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    job_name: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if env:
        params["env"] = env
    if job_name:
        params["job_name"] = job_name
    if status_filter:
        params["status"] = status_filter
    try:
        return await bridge._req("GET", "/learning/job-runs", params=params)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class RunLearningJobsBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^LIVE$")
    suite: Optional[str] = Field(
        default=None,
        description="capture | distill | pricing | audit | quality | nightly | all",
    )
    jobs: Optional[list[str]] = None
    triggered_by: str = Field(default="console:learning", min_length=1, max_length=120)
    limit: int = Field(default=200, ge=1, le=500)
    lookback_days: int = Field(default=7, ge=1, le=30)
    max_results: int = Field(default=100, ge=1, le=500)
    min_pricing_samples: int = Field(default=3, ge=1, le=50)
    dry_run: bool = True


@router.get("/jobs/{job_id}")
async def get_learning_job(
    job_id: str,
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    row = get_job(job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return row


@router.post("/run-jobs")
async def run_learning_jobs(
    body: RunLearningJobsBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    payload = {
        **body.model_dump(),
        "triggered_by": f"console:{user.get('email', 'unknown')}",
    }
    settings = get_settings()
    if settings.learning_async_jobs:

        async def _bridge_call() -> dict[str, Any]:
            return await bridge._req(
                "POST",
                "/learning/run-scheduled-jobs",
                json=payload,
                timeout_sec=_learning_bridge_timeout(bridge),
            )

        job_id = create_job(
            kind="run-scheduled-jobs",
            meta={"suite": body.suite, "env": body.env},
        )
        await run_in_background(job_id, _bridge_call)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "status": "accepted",
                "poll": f"/learning/jobs/{job_id}",
            },
        )
    try:
        return await bridge._req(
            "POST",
            "/learning/run-scheduled-jobs",
            json=payload,
            timeout_sec=_learning_bridge_timeout(bridge),
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class ProposeEditPolicyBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    scope: str = Field(default="company_style", pattern="^(company_style|user_style)$")
    limit: int = Field(default=200, ge=1, le=500)
    owner_user_id: Optional[int] = None


class BackfillEditLearningBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    dry_run: bool = False
    limit: int = Field(default=500, ge=1, le=2000)


@router.post("/backfill-edit-learning")
async def backfill_edit_learning(
    body: BackfillEditLearningBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    payload = {
        "env": body.env,
        "dry_run": body.dry_run,
        "limit": body.limit,
    }
    settings = get_settings()
    if settings.learning_async_jobs:

        async def _bridge_call() -> dict[str, Any]:
            return await bridge._req(
                "POST",
                "/learning/backfill-edit-learning",
                json=payload,
                timeout_sec=_learning_bridge_timeout(bridge),
            )

        job_id = create_job(
            kind="backfill-edit-learning",
            meta={"env": body.env, "dry_run": body.dry_run},
        )
        await run_in_background(job_id, _bridge_call)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "status": "accepted",
                "poll": f"/learning/jobs/{job_id}",
            },
        )
    try:
        return await bridge._req(
            "POST",
            "/learning/backfill-edit-learning",
            json=payload,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/propose-edit-policy")
async def propose_edit_policy(
    body: ProposeEditPolicyBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    payload = {
        "env": body.env,
        "scope": body.scope,
        "updated_by": f"console:{user.get('email', 'unknown')}",
        "limit": body.limit,
    }
    if body.owner_user_id is not None:
        payload["owner_user_id"] = body.owner_user_id
    settings = get_settings()
    if settings.learning_async_jobs:

        async def _bridge_call() -> dict[str, Any]:
            return await bridge._req(
                "POST",
                "/learning/apply-edit-policy",
                json=payload,
                timeout_sec=_learning_bridge_timeout(bridge),
            )

        job_id = create_job(
            kind="apply-edit-policy",
            meta={"scope": body.scope, "env": body.env},
        )
        await run_in_background(job_id, _bridge_call)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "status": "accepted",
                "poll": f"/learning/jobs/{job_id}",
            },
        )
    try:
        return await bridge._req(
            "POST",
            "/learning/apply-edit-policy",
            json=payload,
            timeout_sec=_learning_bridge_timeout(bridge),
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/edit-events")
async def list_edit_events(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=500),
) -> dict[str, Any]:
    params: dict[str, Any] = {"env": env, "limit": limit}
    if identity_id is not None:
        params["identity_id"] = identity_id
    if campaign_id:
        params["campaign_id"] = campaign_id
    try:
        return await bridge._req("GET", "/learning/edit-events", params=params)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/reject-events")
async def list_reject_events(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    identity_id: Optional[int] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    goal: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    params: dict[str, Any] = {"env": env, "limit": limit}
    if identity_id is not None:
        params["identity_id"] = identity_id
    if campaign_id:
        params["campaign_id"] = campaign_id
    if goal:
        params["goal"] = goal
    try:
        return await bridge._req("GET", "/learning/reject-events", params=params)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/edit-distance-trend")
async def edit_distance_trend(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    days: int = Query(default=90, ge=1, le=730),
    bucket: str = Query(default="week", pattern="^(day|week)$"),
    goal: Optional[str] = Query(default=None),
    child_skill: Optional[str] = Query(default=None),
    operator_user_id: Optional[int] = Query(default=None),
) -> dict[str, Any]:
    """Proxy the Bridge convergence metric (edit_distance over time)."""
    params: dict[str, Any] = {"env": env, "days": days, "bucket": bucket}
    if goal:
        params["goal"] = goal
    if child_skill:
        params["child_skill"] = child_skill
    if operator_user_id is not None:
        params["operator_user_id"] = operator_user_id
    try:
        return await bridge._req(
            "GET", "/learning/edit-distance-trend", params=params,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/preview-edit-batch")
async def preview_edit_batch(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    scope: str = Query(default="company_style", pattern="^(company_style|user_style)$"),
    owner_user_id: Optional[int] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    params: dict[str, Any] = {"env": env, "scope": scope, "limit": limit}
    if owner_user_id is not None:
        params["owner_user_id"] = owner_user_id
    try:
        return await bridge._req("GET", "/learning/preview-edit-batch", params=params)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class PolicyMergePreviewBody(BaseModel):
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    proposal: dict[str, Any] = Field(default_factory=dict)


@router.post("/policy-merge-preview")
async def policy_merge_preview(
    body: PolicyMergePreviewBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
) -> dict[str, Any]:
    try:
        return await bridge._req(
            "POST",
            "/learning/policy-merge-preview",
            json=body.model_dump(),
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/policies/{scope}")
async def get_learning_policy(
    scope: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if not _scope_allowed(scope):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"scope must be one of {sorted(LEARNING_POLICY_SCOPES)} "
            f"or start with {DISCOVERY_CRITERIA_PREFIX!r}",
        )
    try:
        return await bridge._req(
            "GET", f"/policies/{scope}", params={"env": env},
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/policies/{scope}/history")
async def get_learning_policy_history(
    scope: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    owner_user_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    if not _scope_allowed(scope):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"scope must be one of {sorted(LEARNING_POLICY_SCOPES)} "
            f"or start with {DISCOVERY_CRITERIA_PREFIX!r}",
        )
    params: dict[str, Any] = {"limit": limit}
    if owner_user_id is not None:
        params["owner_user_id"] = owner_user_id
    try:
        return await bridge._req(
            "GET", f"/policies/{scope}/history", params=params,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class PolicyRollbackBody(BaseModel):
    to_version: int = Field(ge=1)
    owner_user_id: Optional[int] = None
    env: Optional[str] = Field(default=None, pattern="^(TEST|LIVE)$")


@router.post("/policies/{scope}/rollback")
async def rollback_learning_policy(
    scope: str,
    body: PolicyRollbackBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """Roll a learning policy back to a prior version (regression-guard remedy)."""
    if not _scope_allowed(scope):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"scope must be one of {sorted(LEARNING_POLICY_SCOPES)} "
            f"or start with {DISCOVERY_CRITERIA_PREFIX!r}",
        )
    payload: dict[str, Any] = {
        "to_version": body.to_version,
        "updated_by": f"console:{user.get('email', 'unknown')}",
    }
    if body.owner_user_id is not None:
        payload["owner_user_id"] = body.owner_user_id
    if body.env is not None:
        payload["env"] = body.env
    try:
        return await bridge._req(
            "POST", f"/policies/{scope}/rollback", json=payload,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ---------------------------------------------------------------------------
# Discovery decision learning (shortlist approve / remove / transfer)
# ---------------------------------------------------------------------------


@router.get("/discovery-tags")
async def list_discovery_tags(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    action: Optional[str] = Query(default=None, pattern="^(approve|remove|transfer)$"),
    status_filter: str = Query(
        default="active", alias="status", pattern="^(active|proposed|rejected)$",
    ),
) -> dict[str, Any]:
    """Tag vocabulary for the shortlist feedback dialog and learning page."""
    try:
        return await bridge.list_discovery_tags(action=action, status=status_filter)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class DecideDiscoveryTagBody(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    decision: str = Field(pattern="^(approved|rejected)$")


@router.post("/discovery-tags/decide")
async def decide_discovery_tag(
    body: DecideDiscoveryTagBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """Approve / reject a mined tag proposal (becomes selectable when approved)."""
    try:
        return await bridge.decide_discovery_tag(body.model_dump())
    except BridgeError as exc:
        if exc.status == 404:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.detail) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/discovery-feedback-requirements")
async def discovery_feedback_requirements(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    sku: Optional[str] = Query(default=None, max_length=80),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Sample counts + whether the comment is still required for this SPU.

    Always includes ``feedback_required`` (the Console-side kill switch
    ``KOC_DISCOVERY_FEEDBACK_REQUIRED``) so the frontend can relax its dialog
    validation in lockstep with the backend. Degrades instead of 502 when the
    bridge is unreachable — the learning channel must never block shortlist
    operations.
    """
    feedback_required = get_settings().discovery_feedback_required
    try:
        out = await bridge.discovery_feedback_requirements(sku=sku, env=env)
    except BridgeError:
        out = {
            "sku": sku,
            "comment_required": False,
            "tags_required": feedback_required,
            "degraded": True,
        }
    out["feedback_required"] = feedback_required
    return out


@router.get("/shortlist-decision-events")
async def list_shortlist_decision_events(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
    sku: Optional[str] = Query(default=None, max_length=80),
    category: Optional[str] = Query(default=None, max_length=80),
    action: Optional[str] = Query(default=None, pattern="^(approve|remove|transfer)$"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        return await bridge.list_shortlist_decision_events(
            env=env, sku=sku, category=category, action=action, limit=limit,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/pending-discovery-proposals")
async def pending_discovery_proposals(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    try:
        return await bridge.list_pending_discovery_proposals(env=env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/discovery-criteria")
async def discovery_criteria(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    sku: str = Query(min_length=1, max_length=80),
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    """Learned SPU + category criteria for the learning page viewer."""
    try:
        return await bridge.get_discovery_criteria(sku=sku, env=env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/failed-shortlist-captures")
def failed_shortlist_captures(
    _user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
    limit: int = Query(default=30, ge=1, le=200),
    include_replayed: bool = Query(default=False),
) -> dict[str, Any]:
    """Learning samples that failed to persist after approve/remove/transfer."""
    items = list_failed_shortlist_captures(
        conn, limit=limit, include_replayed=include_replayed,
    )
    return {"items": items, "count": len(items)}


class ReplayShortlistCaptureBody(BaseModel):
    audit_id: int = Field(ge=1)


@router.post("/replay-shortlist-capture")
async def replay_shortlist_capture_route(
    body: ReplayShortlistCaptureBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    """One-click replay of a failed learning capture from audit ``replay_body``."""
    return await replay_shortlist_capture(
        bridge,
        conn,
        audit_id=body.audit_id,
        actor_user_id=int(user["id"]),
    )


@router.get("/product-categories")
async def list_product_categories(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
) -> dict[str, Any]:
    try:
        return await bridge.list_product_categories()
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class PutProductCategoryBody(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    product_name: Optional[str] = Field(default=None, max_length=200)


@router.put("/product-categories/{sku}")
async def put_product_category(
    sku: str,
    body: PutProductCategoryBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """Operator correction of a SKU's category (authoritative over LLM)."""
    payload = {
        "category": body.category,
        "product_name": body.product_name,
        "updated_by": f"console:{user.get('email', 'unknown')}",
    }
    try:
        return await bridge.put_product_category(sku, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class PromoteStrategyBody(BaseModel):
    goal: str = Field(min_length=1, max_length=120)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
    scope: str = Field(
        default="reply_strategy", pattern="^(reply_strategy|outcome_strategy)$",
    )
    min_approvals: int = Field(default=2, ge=1, le=50)
    min_age_days: int = Field(default=7, ge=0, le=365)
    dry_run: bool = True


@router.post("/promote-strategy")
async def promote_strategy(
    body: PromoteStrategyBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    if body.goal not in PROMOTABLE_GOALS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"goal must be one of {PROMOTABLE_GOALS}",
        )
    payload = {
        "goal": body.goal,
        "env": body.env,
        "scope": body.scope,
        "min_approvals": body.min_approvals,
        "min_age_days": body.min_age_days,
        "dry_run": body.dry_run,
        "triggered_by": f"console:{user.get('email', 'unknown')}",
    }
    try:
        return await bridge.promote_strategy(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
