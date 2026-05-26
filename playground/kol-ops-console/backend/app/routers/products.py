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
from ..discovery_gate import (
    REDISCOVERY_INSTRUCTIONS,
    evaluate_gate_after_terminal,
)
from ..gateway_client import (
    GatewayClient,
    GatewayError,
    RUNNING_STATES,
    TERMINAL_STATES,
)
from ..product_variants import parse_variants_from_url

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
    *,
    bridge: BridgeClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Reconcile gateway run state for every campaign passed in.

    Three things happen here on every GET-driven invocation:

    1. **Row status flip** — for each campaign with ``status='running'``
       and a ``run_id``, poll the gateway and flip the row to
       ``closed`` / ``cancelled`` when the run reports terminal.

    2. **Multi-run ended_at sync** — poll every other registered run for
       the campaign in ``product_campaign_runs`` that has not yet had
       ``ended_at`` written, and write it when the gateway reports
       terminal. Without this, runs whose ``run_id`` was overwritten on
       the row (e.g. approve-driven outreach overwriting a rediscover
       run_id) never get their ``ended_at`` set, and the transcript
       panel shows them as live forever.

    3. **Discovery gate dispatch** — when the row's ``gate_run_id``
       reaches terminal, dispatch the quantity-gate evaluator. The gate
       fires only on the **discovery-purpose** run; approve-driven
       outreach runs share the row but do NOT trigger the gate (their
       ``run_id`` is separate from ``gate_run_id``). ``cancelled``
       discovery runs are not gated — they clear ``gate_run_id``
       directly. The evaluator either auto-fires a rediscover
       (retry_count < 3) or opens a ``discovery_floor_unmet`` escalation.

    Returns a dict ``{campaign_id: {run_state, run_error}}`` so the caller
    can surface live state in the response without doing another lookup.
    """
    from ..run_registry import list_open_runs_for_campaign, mark_run_ended

    updates: dict[str, dict[str, Any]] = {}
    gate_work: list[dict[str, Any]] = []
    dirty = False

    for r in rows:
        campaign_id = r["campaign_id"]
        env = r["env"]

        # Pull all column values we might consult into locals so the
        # ``in r.keys()`` guard for legacy rows is centralised.
        row_keys = r.keys() if hasattr(r, "keys") else []
        gate_run_id = r["gate_run_id"] if "gate_run_id" in row_keys else None
        target_floor = (
            r["target_floor"] if "target_floor" in row_keys else None
        )

        # ---- (1) Row status flip based on the latest run_id -----------
        if r["status"] == "running" and r["run_id"]:
            try:
                info = await gateway.get_run(r["run_id"])
            except GatewayError:
                info = None
            if info is None:
                # Gateway evicted the run from its in-memory TTL cache
                # (~1h after terminal). Per ``GatewayClient.get_run`` this
                # only happens once a run is long-terminal, so treat as
                # closed — otherwise the row stays ``running`` forever,
                # which blocks /start and confuses the UI.
                updates[campaign_id] = {
                    "run_state": "evicted", "run_error": None
                }
                conn.execute(
                    "UPDATE product_campaigns SET status='closed' "
                    "WHERE campaign_id=? AND env=?",
                    (campaign_id, env),
                )
                dirty = True
            else:
                state = str(info.get("status") or "").lower()
                updates[campaign_id] = {
                    "run_state": state or None,
                    "run_error": info.get("error"),
                }
                if state in TERMINAL_STATES:
                    new_status = "cancelled" if state == "cancelled" else "closed"
                    conn.execute(
                        "UPDATE product_campaigns SET status=? "
                        "WHERE campaign_id=? AND env=?",
                        (new_status, campaign_id, env),
                    )
                    dirty = True

        # ---- (2) Discovery gate run terminal handling -----------------
        # gate_run_id may equal r["run_id"] (no approve yet) or differ
        # (approve overwrote run_id). Poll it independently so the gate
        # fires off the discovery run's terminal state regardless of
        # which run owns the row's ``run_id`` field right now.
        gate_state_str: str | None = None
        if gate_run_id and bridge is not None and target_floor is not None:
            try:
                gate_info = await gateway.get_run(gate_run_id)
            except GatewayError:
                gate_info = None
            if gate_info is None:
                # Gateway evicted the discovery run from its in-memory TTL
                # cache before we observed it reach terminal. Per the
                # gateway contract this only happens for terminal runs, so
                # dispatch the gate evaluator with no run_info — it will
                # re-read the candidate count and decide
                # (floor-met / auto-retry / escalate). Without this branch
                # ``gate_run_id`` would stay set forever and lock the
                # operator out (Approve disabled + Rediscover button
                # gated on ``gate_active=false``).
                gate_state_str = "evicted"
                gate_work.append({
                    "campaign_id": campaign_id,
                    "env": env,
                    "target_floor": int(target_floor),
                    "retry_count": int(r["retry_count"] or 0)
                        if "retry_count" in row_keys else 0,
                    "run_info": None,
                    "gate_run_id": gate_run_id,
                })
                mark_run_ended(conn, run_id=gate_run_id)
                dirty = True
            else:
                gate_state = str(gate_info.get("status") or "").lower()
                gate_state_str = gate_state or None
                if gate_state == "cancelled":
                    # Operator stopped the discovery run intentionally —
                    # do not auto-retry, just release the gate pointer.
                    conn.execute(
                        "UPDATE product_campaigns SET gate_run_id=NULL "
                        "WHERE campaign_id=? AND env=?",
                        (campaign_id, env),
                    )
                    dirty = True
                elif gate_state in TERMINAL_STATES:
                    gate_work.append({
                        "campaign_id": campaign_id,
                        "env": env,
                        "target_floor": int(target_floor),
                        "retry_count": int(r["retry_count"] or 0)
                            if "retry_count" in row_keys else 0,
                        "run_info": gate_info,
                        "gate_run_id": gate_run_id,
                    })
        # Surface gate state on the per-campaign update map so the
        # response can render an "approve disabled while gate active"
        # affordance without an extra DB round-trip. ``gate_active`` is
        # true whenever ``gate_run_id`` is set, regardless of the run's
        # current gateway state — the gate is "active" from the moment
        # a discovery run starts until ``evaluate_gate_after_terminal``
        # decides (floor met / escalated) and clears the pointer. This
        # eliminates the otherwise-fragile window where the discovery
        # run reached terminal but the auto-retry has not yet started.
        entry = updates.setdefault(
            campaign_id, {"run_state": None, "run_error": None}
        )
        entry["gate_run_id"] = gate_run_id
        entry["gate_state"] = gate_state_str
        entry["gate_active"] = bool(gate_run_id)

        # ---- (3) Multi-run ended_at sync ------------------------------
        # Walk every open run on this campaign (including the one we
        # just polled — mark_run_ended is idempotent) and write
        # ended_at on any that report terminal. Bounded by 24h age.
        open_runs = list_open_runs_for_campaign(
            conn, campaign_id=campaign_id, env=env
        )
        for open_run in open_runs:
            run_id_to_poll = open_run["run_id"]
            try:
                rinfo = await gateway.get_run(run_id_to_poll)
            except GatewayError:
                continue
            if rinfo is None:
                # Gateway eviction = long-terminal (see step 1 / 2 above).
                # Write ended_at with now() — we missed the real moment,
                # but anything is better than the transcript panel showing
                # the run as live forever.
                mark_run_ended(conn, run_id=run_id_to_poll)
                dirty = True
                continue
            rstate = str(rinfo.get("status") or "").lower()
            if rstate in TERMINAL_STATES:
                mark_run_ended(conn, run_id=run_id_to_poll)
                dirty = True

    if dirty:
        conn.commit()

    # Dispatch gate work AFTER the status-flip commit so the auto-retry's
    # in-flight 409 check sees fresh state and so concurrent GETs
    # observing the same flip can dedup via the registry + the per-
    # campaign asyncio lock inside ``evaluate_gate_after_terminal``.
    import logging as _logging
    for work in gate_work:
        try:
            await evaluate_gate_after_terminal(
                bridge=bridge,  # type: ignore[arg-type]
                gateway=gateway,
                conn=conn,
                campaign_id=work["campaign_id"],
                env=work["env"],
                target_floor=work["target_floor"],
                retry_count=work["retry_count"],
                run_info=work["run_info"],
                rediscovery_instructions=REDISCOVERY_INSTRUCTIONS,
                gate_run_id=work["gate_run_id"],
            )
        except Exception:  # noqa: BLE001 — gate side-effects must never break GETs
            _logging.getLogger(__name__).exception(
                "discovery gate crashed for %s/%s",
                work["campaign_id"], work["env"],
            )
    return updates


class ProductVariant(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    label: str | None = None
    url: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class ProductBody(BaseModel):
    sku: str = Field(min_length=1)
    name: str
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    # Operator-supplied selling-points + pitch markdown captured upfront so
    # the LaunchCampaignForm can prefill instead of asking each time.
    pitch_md: str | None = Field(default=None, max_length=64_000)
    selling_points: str | None = Field(default=None, max_length=8_000)
    # Known variants (color/size/etc.) the campaign can offer the KOL.
    # Populated either by the operator manually or by /products/parse-variants.
    variants: list[ProductVariant] = Field(default_factory=list)
    # Default budget values surfaced as the LaunchCampaignForm initial state.
    default_budget_per_kol: float | None = None
    default_budget_total: float | None = None
    default_absolute_floor: float | None = None


class ParseVariantsBody(BaseModel):
    url: str = Field(min_length=1, max_length=2_000)


def _row_to_product(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.pop("tags_json") or "[]")
    d["variants"] = json.loads(d.pop("variants_json") or "[]") if d.get("variants_json") is not None else []
    # Surface the wider product facts (None when never set).
    d.setdefault("pitch_md", None)
    d.setdefault("selling_points", None)
    d.setdefault("default_budget_per_kol", None)
    d.setdefault("default_budget_total", None)
    d.setdefault("default_absolute_floor", None)
    return d


@router.get("")
def list_products(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
) -> list[dict]:
    rows = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    return [_row_to_product(r) for r in rows]


@router.post("/parse-variants")
def parse_variants(
    body: ParseVariantsBody,
    _: Annotated[dict, Depends(current_user)],
) -> dict[str, Any]:
    """Best-effort: extract a variant token from a merchant URL.

    Used by the product form so operators can paste a Povison-style link
    (``...?variant=32529``) and have the variant pre-populated as one row in
    the product's variant list. Pages that don't expose a token return an
    empty list — operators add variants manually.
    """
    variants = parse_variants_from_url(body.url)
    return {"url": body.url, "variants": variants}


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
        "SELECT sku, name, url, tags_json, notes, created_at, "
        "pitch_md, selling_points, variants_json, "
        "default_budget_per_kol, default_budget_total, default_absolute_floor "
        "FROM products ORDER BY created_at DESC"
    ).fetchall()
    pc_rows = conn.execute(
        "SELECT sku, campaign_id, env, run_id, status, started_at, "
        "target_floor, baseline_candidate_count, retry_count, "
        "floor_unmet_reason, gate_run_id "
        "FROM product_campaigns WHERE env=?",
        (e,),
    ).fetchall()
    # Reconcile with gateway BEFORE re-reading, so the rollup reflects fresh state.
    await _sync_run_states(conn, gateway, pc_rows, bridge=bridge)
    pc_rows = conn.execute(
        "SELECT sku, campaign_id, env, run_id, status, started_at, "
        "target_floor, baseline_candidate_count, retry_count, "
        "floor_unmet_reason, gate_run_id "
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
        # Surface gate state from the running campaign if any, else the
        # most-recently-started row, so the UI can render a discovery-
        # progress badge without per-campaign drilldown.
        gate_row = active[0] if active else (cs[0] if cs else None)
        latest = _summarize_events(events, product_sku=sku) if cs else {
            "stage": None,
            "sub_status": None,
            "last_event_type": None,
            "last_event_ts": None,
            "kol_identity_ids": [],
            "contacted_kol_ids": [],
            "event_count": 0,
        }
        variants = json.loads(p["variants_json"] or "[]") if p["variants_json"] is not None else []
        out.append({
            "sku": sku,
            "name": p["name"],
            "url": p["url"],
            "tags": json.loads(p["tags_json"] or "[]"),
            "notes": p["notes"],
            "pitch_md": p["pitch_md"],
            "selling_points": p["selling_points"],
            "variants": variants,
            "variant_count": len(variants),
            "default_budget_per_kol": p["default_budget_per_kol"],
            "default_budget_total": p["default_budget_total"],
            "default_absolute_floor": p["default_absolute_floor"],
            "campaigns_total": len(cs),
            "campaigns_running": len(active),
            "active_campaign_ids": [c["campaign_id"] for c in active],
            "stage": latest["stage"],
            "sub_status": latest["sub_status"],
            "last_event_type": latest["last_event_type"],
            "last_event_ts": latest["last_event_ts"],
            "kols_contacted": len(latest.get("contacted_kol_ids", [])),
            "discovery_floor": gate_row["target_floor"] if gate_row else None,
            "discovery_retry_count": (
                gate_row["retry_count"] if gate_row else 0
            ),
            "discovery_floor_unmet_reason": (
                gate_row["floor_unmet_reason"] if gate_row else None
            ),
        })
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
def upsert_product(
    body: ProductBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    variants_payload = [v.model_dump() for v in body.variants]
    conn.execute(
        """INSERT INTO products (
              sku, name, url, tags_json, notes, created_at,
              pitch_md, selling_points, variants_json,
              default_budget_per_kol, default_budget_total, default_absolute_floor)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(sku) DO UPDATE SET
             name=excluded.name, url=excluded.url,
             tags_json=excluded.tags_json, notes=excluded.notes,
             pitch_md=excluded.pitch_md,
             selling_points=excluded.selling_points,
             variants_json=excluded.variants_json,
             default_budget_per_kol=excluded.default_budget_per_kol,
             default_budget_total=excluded.default_budget_total,
             default_absolute_floor=excluded.default_absolute_floor""",
        (
            body.sku, body.name, body.url, json.dumps(body.tags), body.notes, now,
            body.pitch_md, body.selling_points, json.dumps(variants_payload),
            body.default_budget_per_kol, body.default_budget_total,
            body.default_absolute_floor,
        ),
    )
    write_audit(
        conn, actor_user_id=user["id"], action="product.upsert", target=body.sku,
        payload={"variant_count": len(variants_payload)},
    )
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
    return _row_to_product(row)


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
        "SELECT sku, campaign_id, env, run_id, status, started_at, "
        "started_by_user_id, target_floor, baseline_candidate_count, "
        "retry_count, floor_unmet_reason, gate_run_id "
        "FROM product_campaigns WHERE sku=? AND env=? ORDER BY started_at DESC",
        (sku, e),
    ).fetchall()
    run_state_map = await _sync_run_states(conn, gateway, rows, bridge=bridge)
    rows = conn.execute(
        "SELECT sku, campaign_id, env, run_id, status, started_at, "
        "started_by_user_id, target_floor, baseline_candidate_count, "
        "retry_count, floor_unmet_reason, gate_run_id "
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
        # ``pending`` = visible candidates the operator has NOT yet approved
        # (anything except selected_for_outreach). Used by the UI so a
        # rediscover-added candidate still triggers the "Review candidates"
        # button after an earlier round was already approved.
        pending_count = sum(
            1 for c in visible_candidates
            if c.get("candidate_status") != "selected_for_outreach"
        )
        target_floor = r["target_floor"]
        campaigns.append({
            "campaign_id": r["campaign_id"],
            "env": r["env"],
            "run_id": r["run_id"],
            "status": r["status"],
            "started_at": r["started_at"],
            "started_by_user_id": r["started_by_user_id"],
            "run_state": gw.get("run_state"),
            "run_error": gw.get("run_error"),
            # Discovery-purpose run state. ``gate_active=true`` means the
            # quantity gate is still tracking a live rediscover/auto-retry
            # run; the UI uses this to disable Approve so an operator
            # can't truncate the pool mid-discovery.
            "gate_run_id": gw.get("gate_run_id"),
            "gate_state": gw.get("gate_state"),
            "gate_active": bool(gw.get("gate_active")),
            **summary,
            "kol_identity_ids": kol_identity_ids,
            "candidate_count": len(visible_candidates),
            "pending_candidate_count": pending_count,
            "shortlist_ready": summary["shortlist_ready"] or bool(visible_candidates),
            "shortlist_approved": summary["shortlist_approved"] or selected_count > 0,
            "target_floor": target_floor,
            "baseline_candidate_count": r["baseline_candidate_count"],
            "retry_count": r["retry_count"],
            "floor_unmet_reason": r["floor_unmet_reason"],
            "current_candidate_count": len(visible_candidates),
            "floor_progress": (
                None if target_floor is None
                else f"{len(visible_candidates)}/{target_floor}"
            ),
        })

    kols = await _get_identity_map(bridge, needed_ids) if needed_ids else {}
    return {"campaigns": campaigns, "kols": kols}
