"""Shared Nox helpers for Console routers."""

from __future__ import annotations

from typing import Any

from .bridge_client import BridgeClient, BridgeError

NOX_SHORTLIST_FACT_KEYS = (
    "identity.nox_diligence_verdict",
    "identity.nox_cache_month",
    "identity.nox_creator_id",
)


def _nox_fields_from_facts(facts: dict[str, Any] | None) -> dict[str, Any]:
    raw = facts or {}
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
) -> None:
    """Attach Nox diligence summary fields to shortlist rows (in place)."""
    ids = [c["identity_id"] for c in candidates if isinstance(c.get("identity_id"), int)]
    if not ids:
        return

    try:
        by_id = await bridge.batch_facts_subset(
            campaign_id=campaign_id,
            identity_ids=ids,
            env=env,
            fact_keys=list(NOX_SHORTLIST_FACT_KEYS),
        )
    except BridgeError:
        by_id = {}

    for row in candidates:
        iid = row.get("identity_id")
        if not isinstance(iid, int):
            continue
        row.update(_nox_fields_from_facts(by_id.get(iid)))


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

    try:
        by_id = await bridge.batch_facts_subset(
            campaign_id=campaign_id,
            identity_ids=identity_ids,
            env=env,
            fact_keys=["identity.nox_creator_id"],
        )
    except BridgeError:
        by_id = {}

    for iid in identity_ids:
        creator_key: str | None = None
        facts = by_id.get(iid) or {}
        cid = facts.get("identity.nox_creator_id")
        if cid is not None and str(cid).strip():
            creator_key = str(cid).strip()

        if creator_key and creator_key in seen_creators:
            dropped.append(iid)
            continue
        if creator_key:
            seen_creators.add(creator_key)
        unique.append(iid)

    return unique, dropped
