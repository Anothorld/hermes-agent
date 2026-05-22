"""Product (SKU) catalog stored locally."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import (
    GatewayClient,
    GatewayError,
    RUNNING_STATES,
    TERMINAL_STATES,
)

router = APIRouter(prefix="/products", tags=["products"])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


# Subset of stages we care about for "current node" derivation, ordered so
# the *latest event in time* wins (we don't try to be smarter than the bridge).
_KNOWN_STAGES = {
    "discovered",
    "outreach",
    "product_pick",
    "negotiation",
    "contract",
    "logistics",
    "content_delivery",
    "closed",
}


def _summarize_events(
    events: list[dict[str, Any]],
    *,
    campaign_id: str | None = None,
    product_sku: str | None = None,
) -> dict[str, Any]:
    """Reduce a recent-event list to one campaign's current state.

    Inputs are bridge ``/events/recent`` rows. We pick the latest matching
    event by ``ts`` (string ISO-8601 is fine here, lexicographic order matches
    chronological order) and collect distinct ``kol_identity_id``.
    """
    matched: list[dict[str, Any]] = []
    for ev in events:
        if campaign_id is not None and ev.get("campaign_id") != campaign_id:
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        event_sku = ev.get("product_sku") or payload.get("product_sku")
        if product_sku is not None and event_sku != product_sku:
            continue
        matched.append(ev)
    if not matched:
        return {
            "stage": None,
            "sub_status": None,
            "last_event_type": None,
            "last_event_ts": None,
            "kol_identity_ids": [],
            "contacted_kol_ids": [],
            "shortlist_ready": False,
            "shortlist_approved": False,
            "event_count": 0,
        }
    matched.sort(key=lambda e: (e.get("ts") or "", int(e.get("id") or 0)))
    last = matched[-1]
    # Walk backwards to find the most recent known stage label (events like
    # ``alias_added`` may carry no stage).
    stage = None
    sub_status = None
    for ev in reversed(matched):
        s = ev.get("stage")
        if s in _KNOWN_STAGES:
            stage = s
            sub_status = ev.get("sub_status")
            break
    kol_ids: list[int] = []
    seen: set[int] = set()
    for ev in matched:
        kid = ev.get("identity_id")
        if isinstance(kid, int) and kid not in seen:
            seen.add(kid)
            kol_ids.append(kid)
    contacted_ids: list[int] = []
    contacted_seen: set[int] = set()
    draft_event_types = {
        "initial_drafted",
        "kol_initial_outreach_draft_ready",
        "outbound_draft_created",
    }
    for ev in matched:
        if ev.get("event_type") not in draft_event_types:
            continue
        kid = ev.get("identity_id")
        if isinstance(kid, int) and kid not in contacted_seen:
            contacted_seen.add(kid)
            contacted_ids.append(kid)
    has_shortlist = any(
        ev.get("event_type") == "shortlist_ready" for ev in matched
    )
    has_approved = any(ev.get("event_type") == "approved" for ev in matched)
    return {
        "stage": stage,
        "sub_status": sub_status,
        "last_event_type": last.get("event_type"),
        "last_event_ts": last.get("ts"),
        "kol_identity_ids": kol_ids,
        "contacted_kol_ids": contacted_ids,
        "shortlist_ready": has_shortlist,
        "shortlist_approved": has_approved,
        "event_count": len(matched),
    }


async def _get_identity_map(
    bridge: BridgeClient,
    identity_ids: set[int],
) -> dict[str, dict[str, Any]]:
    """Fetch identities by id; tolerate missing rows so campaigns still render."""
    out: dict[str, dict[str, Any]] = {}
    for iid in sorted(identity_ids):
        try:
            ident = await bridge.get_identity(iid)
        except BridgeError:
            continue
        out[str(iid)] = {
            "id": iid,
            "display_name": ident.get("display_name"),
            "primary_handle": ident.get("primary_handle"),
            "platform": ident.get("platform"),
        }
    return out


async def _sync_run_states(
    conn: sqlite3.Connection,
    gateway: GatewayClient,
    rows: list[sqlite3.Row],
) -> dict[str, dict[str, Any]]:
    """For each ``product_campaigns`` row with status='running' + a run_id,
    poll the gateway and auto-flip to 'closed'/'cancelled' on terminal states.

    Returns a dict ``{campaign_id: {run_state, run_error}}`` so the caller
    can surface live state in the response without doing another lookup.
    """
    updates: dict[str, dict[str, Any]] = {}
    dirty = False
    for r in rows:
        if r["status"] != "running" or not r["run_id"]:
            continue
        try:
            info = await gateway.get_run(r["run_id"])
        except GatewayError:
            updates[r["campaign_id"]] = {"run_state": "unknown", "run_error": None}
            continue
        if info is None:
            updates[r["campaign_id"]] = {"run_state": "unknown", "run_error": None}
            continue
        else:
            state = str(info.get("status") or "").lower()
            updates[r["campaign_id"]] = {
                "run_state": state or None,
                "run_error": info.get("error"),
            }
            if state in TERMINAL_STATES:
                new_status = "cancelled" if state == "cancelled" else "closed"
            elif state in RUNNING_STATES:
                continue
            else:
                continue
        conn.execute(
            "UPDATE product_campaigns SET status=? WHERE campaign_id=? AND env=?",
            (new_status, r["campaign_id"], r["env"]),
        )
        dirty = True
    if dirty:
        conn.commit()
    return updates


class ProductBody(BaseModel):
    sku: str = Field(min_length=1)
    name: str
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


@router.get("")
def list_products(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
) -> list[dict]:
    rows = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
        out.append(d)
    return out


@router.get("/summary")
async def list_products_summary(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> list[dict]:
    """List page payload: each product + active-campaign rollup.

    Aggregates from local ``product_campaigns`` (Web-triggered) + bridge
    ``events/recent`` (per-event truth). We don't hit the bridge per product —
    we fetch events once and bucket by ``product_sku``.
    """
    e = _env(env)
    products = conn.execute(
        "SELECT sku, name, url, tags_json, notes, created_at FROM products "
        "ORDER BY created_at DESC"
    ).fetchall()
    pc_rows = conn.execute(
        "SELECT sku, campaign_id, env, run_id, status, started_at "
        "FROM product_campaigns WHERE env=?",
        (e,),
    ).fetchall()
    # Reconcile with gateway BEFORE re-reading, so the rollup reflects fresh state.
    await _sync_run_states(conn, gateway, pc_rows)
    pc_rows = conn.execute(
        "SELECT sku, campaign_id, env, run_id, status, started_at "
        "FROM product_campaigns WHERE env=?",
        (e,),
    ).fetchall()
    by_sku: dict[str, list[sqlite3.Row]] = {}
    for r in pc_rows:
        by_sku.setdefault(r["sku"], []).append(r)

    try:
        events = await bridge.recent_events(e, limit=500)
    except BridgeError:
        events = []

    out: list[dict] = []
    for p in products:
        sku = p["sku"]
        cs = by_sku.get(sku, [])
        active = [c for c in cs if c["status"] == "running"]
        latest = _summarize_events(events, product_sku=sku) if cs else {
            "stage": None,
            "sub_status": None,
            "last_event_type": None,
            "last_event_ts": None,
            "kol_identity_ids": [],
            "contacted_kol_ids": [],
            "event_count": 0,
        }
        out.append({
            "sku": sku,
            "name": p["name"],
            "url": p["url"],
            "tags": json.loads(p["tags_json"] or "[]"),
            "notes": p["notes"],
            "campaigns_total": len(cs),
            "campaigns_running": len(active),
            "active_campaign_ids": [c["campaign_id"] for c in active],
            "stage": latest["stage"],
            "sub_status": latest["sub_status"],
            "last_event_type": latest["last_event_type"],
            "last_event_ts": latest["last_event_ts"],
            "kols_contacted": len(latest.get("contacted_kol_ids", [])),
        })
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
def upsert_product(
    body: ProductBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO products (sku, name, url, tags_json, notes, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(sku) DO UPDATE SET
             name=excluded.name, url=excluded.url,
             tags_json=excluded.tags_json, notes=excluded.notes""",
        (body.sku, body.name, body.url, json.dumps(body.tags), body.notes, now),
    )
    write_audit(conn, actor_user_id=user["id"], action="product.upsert", target=body.sku)
    return {"ok": True, "sku": body.sku}


@router.get("/{sku}")
def get_product(
    sku: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
) -> dict:
    row = conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sku not found")
    d = dict(row)
    d["tags"] = json.loads(d.pop("tags_json") or "[]")
    return d


@router.get("/{sku}/campaigns")
async def list_product_campaigns(
    sku: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    """Detail page payload: campaigns for one SKU + per-campaign state.

    Each campaign carries: db row + derived stage / sub_status / last event
    summary + KOL identities (joined to bridge ``/identities`` for handle
    rendering). Returns ``{"campaigns": [...], "kols": {id: identity}}`` so
    the UI can render handles without N+1 fetches.
    """
    e = _env(env)
    if conn.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sku not found")

    rows = conn.execute(
        "SELECT campaign_id, env, run_id, status, started_at, started_by_user_id "
        "FROM product_campaigns WHERE sku=? AND env=? ORDER BY started_at DESC",
        (sku, e),
    ).fetchall()
    run_state_map = await _sync_run_states(conn, gateway, rows)
    rows = conn.execute(
        "SELECT campaign_id, env, run_id, status, started_at, started_by_user_id "
        "FROM product_campaigns WHERE sku=? AND env=? ORDER BY started_at DESC",
        (sku, e),
    ).fetchall()

    try:
        events = await bridge.recent_events(e, limit=500)
    except BridgeError:
        events = []

    needed_ids: set[int] = set()
    campaigns: list[dict] = []
    for r in rows:
        summary = _summarize_events(events, campaign_id=r["campaign_id"], product_sku=sku)
        try:
            candidates = await bridge.list_candidates(r["campaign_id"], env=e)
        except BridgeError:
            candidates = []
        visible_candidates = [
            c for c in candidates if c.get("candidate_status") not in {"rejected", "archived"}
        ]
        selected_count = sum(
            1 for c in visible_candidates
            if c.get("candidate_status") == "selected_for_outreach"
        )
        candidate_ids = [
            c.get("identity_id") for c in visible_candidates if isinstance(c.get("identity_id"), int)
        ]
        kol_identity_ids = list(dict.fromkeys([*summary["kol_identity_ids"], *candidate_ids]))
        needed_ids.update(kol_identity_ids)
        gw = run_state_map.get(r["campaign_id"], {})
        campaigns.append({
            "campaign_id": r["campaign_id"],
            "env": r["env"],
            "run_id": r["run_id"],
            "status": r["status"],
            "started_at": r["started_at"],
            "started_by_user_id": r["started_by_user_id"],
            "run_state": gw.get("run_state"),
            "run_error": gw.get("run_error"),
            **summary,
            "kol_identity_ids": kol_identity_ids,
            "candidate_count": len(visible_candidates),
            "shortlist_ready": summary["shortlist_ready"] or bool(visible_candidates),
            "shortlist_approved": summary["shortlist_approved"] or selected_count > 0,
        })

    kols = await _get_identity_map(bridge, needed_ids) if needed_ids else {}
    return {"campaigns": campaigns, "kols": kols}
