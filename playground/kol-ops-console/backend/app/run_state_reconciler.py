"""Background reconciliation of gateway run state + discovery gate dispatch."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any, Optional

from .bridge_client import BridgeClient, BridgeError
from .config import Settings, get_settings
from .db import _connect
from .deps import get_bridge_singleton, get_gateway_singleton
from .discovery_gate import REDISCOVERY_INSTRUCTIONS, evaluate_gate_after_terminal
from .gateway_client import GatewayClient, GatewayError, TERMINAL_STATES
from .perf_snapshot import perf
from .post_email_discover_draft import (
    maybe_trigger_outreach_draft_after_email_discover,
)
from .run_status_cache import run_status_cache

log = logging.getLogger(__name__)

_run_state_cache: dict[tuple[str, str], dict[str, Any]] = {}
_reconciler_task: Optional[asyncio.Task] = None
_reconcile_lock = asyncio.Lock()


def get_cached_run_updates(
    rows: list[sqlite3.Row],
) -> dict[str, dict[str, Any]]:
    """Return last reconciled per-campaign run state for GET handlers."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = (r["campaign_id"], r["env"])
        cached = _run_state_cache.get(key)
        if cached is not None:
            out[r["campaign_id"]] = dict(cached)
            continue
        row_keys = r.keys() if hasattr(r, "keys") else []
        gate_run_id = r["gate_run_id"] if "gate_run_id" in row_keys else None
        out[r["campaign_id"]] = {
            "run_state": None,
            "run_error": None,
            "gate_run_id": gate_run_id,
            "gate_state": None,
            "gate_active": bool(gate_run_id),
        }
    return out


async def reconcile_run_states(
    conn: sqlite3.Connection,
    gateway: GatewayClient,
    rows: list[sqlite3.Row],
    *,
    bridge: BridgeClient | None = None,
    dispatch_gate: bool = True,
) -> dict[str, dict[str, Any]]:
    """Poll gateway, sync registry, optionally dispatch discovery gate."""
    from .run_registry import list_open_runs_for_campaign, mark_run_ended

    updates: dict[str, dict[str, Any]] = {}
    gate_work: list[dict[str, Any]] = []
    email_discover_followups: list[dict[str, Any]] = []
    dirty = False

    for r in rows:
        campaign_id = r["campaign_id"]
        env = r["env"]
        row_keys = r.keys() if hasattr(r, "keys") else []
        gate_run_id = r["gate_run_id"] if "gate_run_id" in row_keys else None
        target_floor = (
            r["target_floor"] if "target_floor" in row_keys else None
        )

        run_id = r["run_id"]
        if (
            r["status"] == "running"
            and run_id
            and not str(run_id).startswith("pending:")
        ):
            try:
                info = await run_status_cache.get_run(gateway, str(run_id))
            except GatewayError:
                info = None
            if info is None:
                updates[campaign_id] = {"run_state": "evicted", "run_error": None}
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

        gate_state_str: str | None = None
        if (
            gate_run_id
            and not str(gate_run_id).startswith("pending:")
            and bridge is not None
            and target_floor is not None
        ):
            try:
                gate_info = await run_status_cache.get_run(gateway, str(gate_run_id))
            except GatewayError:
                gate_info = None
            if gate_info is None:
                gate_state_str = "evicted"
                if dispatch_gate:
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
                    conn.execute(
                        "UPDATE product_campaigns SET gate_run_id=NULL "
                        "WHERE campaign_id=? AND env=?",
                        (campaign_id, env),
                    )
                    dirty = True
                elif gate_state in TERMINAL_STATES and dispatch_gate:
                    gate_work.append({
                        "campaign_id": campaign_id,
                        "env": env,
                        "target_floor": int(target_floor),
                        "retry_count": int(r["retry_count"] or 0)
                            if "retry_count" in row_keys else 0,
                        "run_info": gate_info,
                        "gate_run_id": gate_run_id,
                    })

        entry = updates.setdefault(
            campaign_id, {"run_state": None, "run_error": None}
        )
        entry["gate_run_id"] = gate_run_id
        entry["gate_state"] = gate_state_str
        entry["gate_active"] = bool(gate_run_id)

        open_runs = list_open_runs_for_campaign(
            conn, campaign_id=campaign_id, env=env,
        )
        for open_run in open_runs:
            run_id_to_poll = open_run["run_id"]
            if not run_id_to_poll or str(run_id_to_poll).startswith("pending:"):
                continue
            try:
                rinfo = await run_status_cache.get_run(gateway, str(run_id_to_poll))
            except GatewayError:
                continue
            if rinfo is None:
                mark_run_ended(conn, run_id=run_id_to_poll)
                dirty = True
                continue
            rstate = str(rinfo.get("status") or "").lower()
            if rstate in TERMINAL_STATES:
                mark_run_ended(conn, run_id=run_id_to_poll)
                dirty = True
                session_id = str(open_run.get("session_id") or "")
                if (
                    rstate == "completed"
                    and session_id.startswith("kol-email-discover:")
                ):
                    email_discover_followups.append({
                        "campaign_id": campaign_id,
                        "env": env,
                        "session_id": session_id,
                        "discover_run_id": str(run_id_to_poll),
                    })

        _run_state_cache[(campaign_id, env)] = dict(entry)

    if dirty:
        conn.commit()

    if bridge is not None:
        for followup in email_discover_followups:
            try:
                await maybe_trigger_outreach_draft_after_email_discover(
                    bridge=bridge,
                    gateway=gateway,
                    conn=conn,
                    campaign_id=followup["campaign_id"],
                    env=followup["env"],
                    session_id=followup["session_id"],
                    discover_run_id=followup["discover_run_id"],
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "auto-draft after email discover failed for %s/%s",
                    followup.get("campaign_id"),
                    followup.get("discover_run_id"),
                )
        if email_discover_followups:
            conn.commit()

    if dispatch_gate and bridge is not None:
        for work in gate_work:
            try:
                await evaluate_gate_after_terminal(
                    bridge=bridge,
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
            except Exception:  # noqa: BLE001
                log.exception(
                    "discovery gate crashed for %s/%s",
                    work["campaign_id"], work["env"],
                )
    return updates


async def _reconcile_once(settings: Settings) -> None:
    async with _reconcile_lock:
        started = time.perf_counter()
        conn = _connect(settings.db_path)
        try:
            rows = conn.execute(
                "SELECT sku, campaign_id, env, run_id, status, started_at, "
                "target_floor, baseline_candidate_count, retry_count, "
                "floor_unmet_reason, gate_run_id "
                "FROM product_campaigns",
            ).fetchall()
            if not rows:
                return
            gateway = get_gateway_singleton()
            bridge = get_bridge_singleton()
            await reconcile_run_states(
                conn, gateway, rows, bridge=bridge, dispatch_gate=True,
            )
            perf.reconciler_runs_total += 1
            perf.reconciler_last_duration_ms = (
                (time.perf_counter() - started) * 1000.0
            )
            perf.reconciler_last_at = time.time()
        finally:
            conn.close()


async def _loop(settings: Settings) -> None:
    interval = max(5.0, settings.run_reconciler_interval_sec)
    while True:
        try:
            await asyncio.sleep(interval)
            await _reconcile_once(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("run state reconciler tick failed")


async def start_reconciler(settings: Optional[Settings] = None) -> None:
    global _reconciler_task
    s = settings or get_settings()
    if not s.run_reconciler_enabled:
        return
    if _reconciler_task is not None:
        return
    _reconciler_task = asyncio.create_task(_loop(s))


async def stop_reconciler() -> None:
    global _reconciler_task
    if _reconciler_task is None:
        return
    _reconciler_task.cancel()
    try:
        await _reconciler_task
    except asyncio.CancelledError:
        pass
    _reconciler_task = None
