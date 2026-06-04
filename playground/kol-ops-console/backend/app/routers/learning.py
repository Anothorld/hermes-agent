"""Proxy Bridge learning endpoints for the Console UI."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, require_role

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
    "pricing_calibration",
})


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
    try:
        return await bridge._req(
            "POST",
            "/learning/backfill-edit-learning",
            json={
                "env": body.env,
                "dry_run": body.dry_run,
                "limit": body.limit,
            },
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


@router.get("/policies/{scope}")
async def get_learning_policy(
    scope: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str = Query(default="LIVE", pattern="^(TEST|LIVE)$"),
) -> dict[str, Any]:
    if scope not in LEARNING_POLICY_SCOPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"scope must be one of {sorted(LEARNING_POLICY_SCOPES)}",
        )
    try:
        return await bridge._req(
            "GET", f"/policies/{scope}", params={"env": env},
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class PromoteStrategyBody(BaseModel):
    goal: str = Field(min_length=1, max_length=120)
    env: str = Field(default="LIVE", pattern="^(TEST|LIVE)$")
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
        "min_approvals": body.min_approvals,
        "min_age_days": body.min_age_days,
        "dry_run": body.dry_run,
        "triggered_by": f"console:{user.get('email', 'unknown')}",
    }
    try:
        return await bridge.promote_strategy(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
