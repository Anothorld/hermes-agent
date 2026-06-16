"""Admin: TEST wipe + audit log read."""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import get_bridge, get_conn, require_role
from ..gate_metrics_audit import compute_gate_audit_metrics
from ..gate_metrics_trends import compute_audit_metric_trends
from ..kol_registry_export import build_registry_xlsx
from ..perf_snapshot import perf
from ..run_launch_queue import launch_queue

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/perf-snapshot")
def perf_snapshot(
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """In-process counters for gateway queue, watcher, WS, and reconciler."""
    settings = get_settings()
    return {
        "env": settings.env,
        "perf": perf.as_dict(),
        "launch_queue": launch_queue.snapshot(),
        "flags": {
            "gateway_launch_queue_enabled": settings.gateway_launch_queue_enabled,
            "gateway_launch_max_inflight": settings.gateway_launch_max_inflight,
            "run_reconciler_enabled": settings.run_reconciler_enabled,
            "sync_run_states_on_get": settings.sync_run_states_on_get,
            "approval_watch_mode": settings.approval_watch_mode,
            "agent_stream_max_runs": settings.agent_stream_max_runs,
            "nox_max_concurrent": settings.nox_max_concurrent,
        },
    }


@router.post("/wipe-test")
async def wipe_test(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner"))],
) -> dict:
    try:
        return await bridge.wipe_test()
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/audit")
def audit(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(require_role("owner"))],
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
        out.append(d)
    return out


@router.get("/gate-metrics")
async def gate_metrics(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
    env: str = Query("TEST"),
    days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """Read-only gate metrics for approvals/escalations operational quality."""
    env_norm = env.upper()
    audit_metrics = compute_gate_audit_metrics(conn, env=env_norm, days=days)

    try:
        discovery = await bridge.get_kol_registry_summary(env=env_norm)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return {
        "env": env_norm,
        "window_days": days,
        "metrics": {
            "first_pass_approval_rate": audit_metrics["first_pass_approval_rate"],
            "avg_handle_minutes": audit_metrics["avg_handle_minutes"],
            "manual_touchpoints_per_campaign": audit_metrics[
                "manual_touchpoints_per_campaign"
            ],
            "termination_rate": audit_metrics["termination_rate"],
            "live_incident_rate": audit_metrics["live_reject_rate"],
        },
        "audit_meta": {
            "first_pass_decisions_total": audit_metrics["first_pass_decisions_total"],
            "reply_decisions_total": audit_metrics["reply_decisions_total"],
            "handle_time_samples": audit_metrics["handle_time_samples"],
            "touched_campaign_count": audit_metrics["touched_campaign_count"],
        },
        "kol_discovery_summary": {
            "discovered_total": int(discovery.get("discovered_total") or 0),
            "passed_count": int(discovery.get("passed_count") or 0),
            "pending_count": int(discovery.get("pending_count") or 0),
            "rejected_count": int(discovery.get("rejected_count") or 0),
            "other_count": int(discovery.get("other_count") or 0),
            "pass_rate": float(discovery.get("pass_rate") or 0.0),
            "initial_outreach_draft_count": int(
                discovery.get("initial_outreach_draft_count") or 0,
            ),
            "initial_outreach_reply_count": int(
                discovery.get("initial_outreach_reply_count") or 0,
            ),
            "automated_reply_excluded_count": int(
                discovery.get("automated_reply_excluded_count") or 0,
            ),
            "pending_reply_count": int(discovery.get("pending_reply_count") or 0),
            "initial_outreach_reply_rate": float(
                discovery.get("initial_outreach_reply_rate") or 0.0,
            ),
            "by_status": discovery.get("by_status") or {},
        },
        "top_rejection_tags": audit_metrics["top_rejection_tags"],
    }


@router.get("/gate-metrics/trends")
async def gate_metrics_trends(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
    env: str = Query("TEST"),
    bucket: str = Query("week", pattern="^(day|week|month|year)$"),
    periods: int | None = Query(None, ge=1, le=90),
) -> dict[str, Any]:
    """Time-bucketed series for all gate-metrics cards (read-only)."""
    env_norm = env.upper()
    try:
        audit_trends = compute_audit_metric_trends(
            conn, env=env_norm, bucket=bucket, periods=periods,
        )
        discovery_trends = await bridge.get_kol_registry_summary_trend(
            env=env_norm, bucket=bucket, periods=periods,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    series: dict[str, list[dict[str, Any]]] = dict(audit_trends.get("series") or {})
    for key, points in (discovery_trends.get("series") or {}).items():
        series[key] = points

    return {
        "env": env_norm,
        "bucket": audit_trends.get("bucket", bucket),
        "periods": audit_trends.get("periods"),
        "series": series,
    }


@router.get("/kol-registry")
async def kol_registry(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
    env: str = Query("TEST"),
    q: str | None = Query(None, max_length=200),
    source: str = Query("all", pattern="^(all|legacy|discovery)$"),
    sort: str = Query("ingested_at", pattern="^(ingested_at|first_discovered_at|created_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Paginated Agent红人列表 — Agent-discovered KOLs only."""
    env_norm = env.upper()
    try:
        out = await bridge.list_kol_registry(
            env=env_norm, q=q, source=source, sort=sort, order=order,
            limit=limit, offset=offset,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    campaign_ids = sorted({
        str(row.get("latest_campaign_id"))
        for row in out.get("items", [])
        if isinstance(row, dict) and row.get("latest_campaign_id")
    })
    sku_by_campaign: dict[str, str | None] = {}
    if campaign_ids:
        placeholders = ",".join("?" * len(campaign_ids))
        rows = conn.execute(
            f"SELECT campaign_id, sku FROM product_campaigns "
            f"WHERE env=? AND campaign_id IN ({placeholders})",
            (env_norm, *campaign_ids),
        ).fetchall()
        sku_by_campaign = {str(r["campaign_id"]): str(r["sku"]) for r in rows}

    items = []
    for row in out.get("items", []):
        if not isinstance(row, dict):
            continue
        cid = row.get("latest_campaign_id")
        campaign_sku = sku_by_campaign.get(str(cid)) if cid else None
        fact_spu = row.get("target_spu")
        target_spu = campaign_sku or fact_spu
        items.append({
            **row,
            "target_spu": target_spu,
        })
    return {
        "env": env_norm,
        "source": out.get("source", source),
        "total": out.get("total", 0),
        "counts": out.get("counts") or {},
        "limit": out.get("limit", limit),
        "offset": out.get("offset", offset),
        "items": items,
    }


@router.get("/kol-registry/export")
async def kol_registry_export(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(require_role("owner", "operator"))],
    env: str = Query("TEST"),
    q: str | None = Query(None, max_length=200),
    source: str = Query("all", pattern="^(all|legacy|discovery)$"),
) -> Response:
    """Download full KOL registry as .xlsx (matches Agent红人列表 template columns)."""
    try:
        content, filename, _row_count = await build_registry_xlsx(
            bridge, conn, env=env.upper(), q=q, source=source,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
