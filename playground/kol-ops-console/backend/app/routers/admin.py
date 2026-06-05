"""Admin: TEST wipe + audit log read."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from collections import Counter, defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from ..bridge_client import BridgeClient, BridgeError
from ..deps import get_bridge, get_conn, require_role
from ..kol_registry_export import build_registry_xlsx

router = APIRouter(prefix="/admin", tags=["admin"])


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
    rows = conn.execute(
        "SELECT action, target, payload_json, ts FROM audit_log "
        "WHERE ts >= datetime('now', ?) ORDER BY id DESC",
        (f"-{days} day",),
    ).fetchall()
    env_norm = env.upper()
    decisions_total = 0
    decisions_approved = 0
    live_decisions = 0
    live_rejected = 0
    total_handle_seconds = 0.0
    total_handle_samples = 0
    tag_counter: Counter[str] = Counter()
    touches_by_campaign: dict[str, int] = defaultdict(int)
    terminated_count = 0
    resolved_count = 0

    for row in rows:
        action = str(row["action"] or "")
        payload = json.loads(row["payload_json"] or "{}")
        payload_env = str(payload.get("env") or env_norm).upper()
        if payload_env != env_norm:
            continue

        if action in {"approval.approve", "approval.reject"}:
            decisions_total += 1
            if action == "approval.approve":
                decisions_approved += 1
            if payload_env == "LIVE":
                live_decisions += 1
                if action == "approval.reject":
                    live_rejected += 1
            cid = payload.get("campaign_id")
            if isinstance(cid, str) and cid:
                touches_by_campaign[cid] += 1
            for tag in payload.get("reason_tags") or []:
                if isinstance(tag, str) and tag.strip():
                    tag_counter[tag.strip().lower()] += 1

        if action == "approval.refine":
            cid = payload.get("campaign_id")
            if isinstance(cid, str) and cid:
                touches_by_campaign[cid] += 1

        if action == "escalation.resolve":
            resolved_count += 1
            if payload.get("decision") == "terminate":
                terminated_count += 1
            for tag in payload.get("reason_tags") or []:
                if isinstance(tag, str) and tag.strip():
                    tag_counter[tag.strip().lower()] += 1

        # Best-effort handle-time approximation: from created/opened ts to decision ts.
        if action in {"approval.approve", "approval.reject", "escalation.resolve"}:
            ts = row["ts"]
            if isinstance(ts, str):
                decided_at = ts.replace("Z", "+00:00")
                try:
                    decided_dt = _dt.datetime.fromisoformat(decided_at)
                except ValueError:
                    decided_dt = None
                created_at = payload.get("opened_at") or payload.get("created_at")
                if decided_dt is not None and isinstance(created_at, str):
                    try:
                        created_dt = _dt.datetime.fromisoformat(
                            created_at.replace("Z", "+00:00"),
                        )
                    except ValueError:
                        created_dt = None
                    if created_dt is not None:
                        delta = (decided_dt - created_dt).total_seconds()
                        if delta >= 0:
                            total_handle_seconds += delta
                            total_handle_samples += 1

    try:
        escalations = await bridge.list_escalations(env=env_norm)
        funnel = await bridge.get_kol_registry_funnel(env=env_norm, days=days)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    re_escalated = sum(1 for r in escalations if isinstance(r, dict) and r.get("state") == "re_escalated")
    escal_total = sum(1 for r in escalations if isinstance(r, dict))

    avg_manual_touches = 0.0
    if touches_by_campaign:
        avg_manual_touches = sum(touches_by_campaign.values()) / len(touches_by_campaign)

    return {
        "env": env_norm,
        "window_days": days,
        "metrics": {
            "first_pass_approval_rate": (
                (decisions_approved / decisions_total) if decisions_total else 0.0
            ),
            "avg_handle_minutes": (
                (total_handle_seconds / 60.0 / total_handle_samples)
                if total_handle_samples else 0.0
            ),
            "re_escalation_rate": (re_escalated / escal_total) if escal_total else 0.0,
            "manual_touchpoints_per_campaign": avg_manual_touches,
            "termination_rate": (terminated_count / resolved_count) if resolved_count else 0.0,
            "live_incident_rate": (live_rejected / live_decisions) if live_decisions else 0.0,
            "kol_candidate_adoption_rate": float(
                funnel.get("kol_candidate_adoption_rate") or 0.0,
            ),
            "initial_outreach_reply_rate": float(
                funnel.get("initial_outreach_reply_rate") or 0.0,
            ),
        },
        "kol_funnel": {
            "discovered_total": int(funnel.get("discovered_total") or 0),
            "prior_collab_excluded": int(funnel.get("prior_collab_excluded") or 0),
            "eligible_total": int(funnel.get("eligible_total") or 0),
            "initial_outreach_draft_count": int(
                funnel.get("initial_outreach_draft_count") or 0,
            ),
            "initial_outreach_reply_count": int(
                funnel.get("initial_outreach_reply_count") or 0,
            ),
        },
        "top_rejection_tags": [
            {"tag": tag, "count": count}
            for tag, count in tag_counter.most_common(10)
        ],
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
