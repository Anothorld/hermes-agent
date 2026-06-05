"""Link preview API for social profiles blocked in iframes."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge
from ..link_preview import fetch_link_preview
from ..profile_og_cache import link_preview_from_facts
from ..shortlist_profile_og import persist_profile_og_cache

router = APIRouter(prefix="/link-preview", tags=["link-preview"])


@router.get("")
async def get_link_preview(
    url: Annotated[str, Query(min_length=8, max_length=2048)],
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    identity_id: Optional[int] = Query(None, ge=1),
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
) -> dict:
    """Return OG card data; reads/writes CAL cache when identity_id is set."""
    facts: dict = {}
    if identity_id is not None:
        try:
            resp = await bridge.read_facts(identity_id, campaign_id=None, env=env)
            raw = resp.get("facts") if isinstance(resp, dict) else {}
            if isinstance(raw, dict):
                facts = raw
        except BridgeError:
            facts = {}

    cached = link_preview_from_facts(facts, url)
    if cached is not None:
        return cached

    result = await fetch_link_preview(url)
    if result.get("reason") == "host_not_allowed":
        raise HTTPException(400, "URL host is not allowed for preview")

    if identity_id is not None and result.get("ok"):
        await persist_profile_og_cache(
            bridge,
            identity_id=identity_id,
            env=env,
            profile_url=url,
            preview=result,
        )
        result = {**result, "persisted": True}

    return result
