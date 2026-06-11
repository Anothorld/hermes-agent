"""Cross-campaign SKU context: prior shortlist approvals for the same product.

With the one-campaign-per-product constraint this is a legacy-data guard:
sibling campaigns can no longer be created, but historical multi-campaign
SKUs may still exist until merged via ``scripts/ops/merge_campaigns.py``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .bridge_client import BridgeClient, BridgeError


def sibling_campaign_ids(
    conn: sqlite3.Connection,
    *,
    sku: str,
    env: str,
    exclude_campaign_id: str,
) -> list[str]:
    """Other console campaigns for the same SKU + env."""
    rows = conn.execute(
        "SELECT campaign_id FROM product_campaigns "
        "WHERE sku=? AND env=? AND campaign_id != ? "
        "ORDER BY started_at DESC",
        (sku, env, exclude_campaign_id),
    ).fetchall()
    return [str(r["campaign_id"]) for r in rows]


async def prior_sku_approval_by_identity(
    conn: sqlite3.Connection,
    bridge: BridgeClient,
    *,
    sku: str,
    env: str,
    exclude_campaign_id: str,
) -> dict[int, dict[str, Any]]:
    """Map identity_id → prior approval metadata from sibling campaigns."""
    out: dict[int, dict[str, Any]] = {}
    sibling_ids = sibling_campaign_ids(
        conn, sku=sku, env=env, exclude_campaign_id=exclude_campaign_id,
    )
    for cid in sibling_ids:
        try:
            rows = await bridge.list_candidate_handles(cid, env=env)
        except BridgeError:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("candidate_status") != "selected_for_outreach":
                continue
            iid = row.get("identity_id")
            if not isinstance(iid, int) or iid in out:
                continue
            out[iid] = {
                "campaign_id": cid,
                "selected_at": row.get("selected_at"),
            }
    return out


def count_prior_sku_dupes_in_pool(
    rows: list[dict[str, Any]],
    prior_by_identity: dict[int, dict[str, Any]],
) -> int:
    """Rows in ``rows`` that are pending here but approved in a sibling campaign."""
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("candidate_status") == "selected_for_outreach":
            continue
        iid = row.get("identity_id")
        if isinstance(iid, int) and iid in prior_by_identity:
            n += 1
    return n


async def prior_sku_approved_handles(
    conn: sqlite3.Connection,
    bridge: BridgeClient,
    *,
    sku: str,
    env: str,
    exclude_campaign_id: str,
) -> list[str]:
    """Normalized handles approved in sibling campaigns for the same SKU."""
    by_id = await prior_sku_approval_by_identity(
        conn, bridge, sku=sku, env=env, exclude_campaign_id=exclude_campaign_id,
    )
    if not by_id:
        return []
    brief_map = await bridge.batch_identity_briefs(list(by_id))
    handles: list[str] = []
    seen: set[str] = set()
    for iid in by_id:
        ident = brief_map.get(iid) or {}
        handle = str(ident.get("primary_handle") or "").strip().lstrip("@").lower()
        if handle and handle not in seen:
            seen.add(handle)
            handles.append(handle)
    return handles
