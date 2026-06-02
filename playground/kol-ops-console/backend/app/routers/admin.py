"""Admin: TEST wipe + audit log read."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from collections import Counter, defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..bridge_client import BridgeClient, BridgeError
from ..deps import get_bridge, get_conn, require_role

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
        },
        "top_rejection_tags": [
            {"tag": tag, "count": count}
            for tag, count in tag_counter.most_common(10)
        ],
    }
