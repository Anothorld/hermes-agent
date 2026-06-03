"""Shared Nox helpers for Console routers."""

from __future__ import annotations

import asyncio
from typing import Any

from .bridge_client import BridgeClient, BridgeError


def _nox_fields_from_facts(facts_resp: dict[str, Any] | None) -> dict[str, Any]:
    raw = (facts_resp or {}).get("facts") if isinstance(facts_resp, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    verdict = raw.get("identity.nox_diligence_verdict")
    month = raw.get("identity.nox_cache_month")
    creator_id = raw.get("identity.nox_creator_id")
    return {
        "nox_diligence_verdict": str(verdict).strip() if verdict else None,
        "nox_cache_month": str(month).strip() if month else None,
        "nox_creator_id": str(creator_id).strip() if creator_id else None,
    }


async def enrich_shortlist_with_nox(
    bridge: BridgeClient,
    candidates: list[dict[str, Any]],
    *,
    campaign_id: str,
    env: str,
    concurrency: int = 8,
) -> None:
    """Attach Nox diligence summary fields to shortlist rows (in place)."""
    ids = [c["identity_id"] for c in candidates if isinstance(c.get("identity_id"), int)]
    if not ids:
        return

    sem = asyncio.Semaphore(max(1, concurrency))
    by_id: dict[int, dict[str, Any]] = {}

    async def _load(iid: int) -> None:
        async with sem:
            try:
                resp = await bridge.read_facts(iid, campaign_id=campaign_id, env=env)
            except BridgeError:
                resp = None
            by_id[iid] = _nox_fields_from_facts(resp)

    await asyncio.gather(*[_load(i) for i in ids])

    for row in candidates:
        iid = row.get("identity_id")
        if not isinstance(iid, int):
            continue
        row.update(by_id.get(iid) or {})


async def dedup_identity_ids_by_nox_creator(
    bridge: BridgeClient,
    identity_ids: list[int],
    *,
    campaign_id: str,
    env: str,
) -> tuple[list[int], list[int]]:
    """Drop later identity_ids that share the same ``identity.nox_creator_id``."""
    unique: list[int] = []
    dropped: list[int] = []
    seen_creators: set[str] = set()

    for iid in identity_ids:
        creator_key: str | None = None
        try:
            resp = await bridge.read_facts(iid, campaign_id=campaign_id, env=env)
            raw = (resp or {}).get("facts") if isinstance(resp, dict) else {}
            if isinstance(raw, dict):
                cid = raw.get("identity.nox_creator_id")
                if cid is not None and str(cid).strip():
                    creator_key = str(cid).strip()
        except BridgeError:
            pass

        if creator_key and creator_key in seen_creators:
            dropped.append(iid)
            continue
        if creator_key:
            seen_creators.add(creator_key)
        unique.append(iid)

    return unique, dropped
