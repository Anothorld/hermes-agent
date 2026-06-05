"""Atomic Veedcrawl fetch + monthly persist + optional CAL index writes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Optional

from . import veedcrawl_cache, veedcrawl_facts

log = logging.getLogger(__name__)

FetchFn = Callable[[], Any]


def should_persist_response(operation: str, response: Any) -> bool:
    """Return False for incomplete async job payloads (e.g. extract with wait=false)."""
    if operation != "extract_from_video":
        return True
    if not isinstance(response, dict):
        return False
    status = str(response.get("status") or "").lower()
    if status == "completed":
        return True
    api = response.get("api_response")
    if isinstance(api, dict) and str(api.get("status") or "").lower() == "completed":
        return True
    return False


def fetch_with_persist(
    *,
    operation: str,
    request: Mapping[str, Any],
    fetch_fn: FetchFn,
    env: str = "LIVE",
    identity_id: Optional[int] = None,
    handle: Optional[str] = None,
    force_refresh: bool = False,
    tz_name: str = veedcrawl_cache.DEFAULT_TIMEZONE,
    write_cal_facts: bool = True,
) -> dict[str, Any]:
    """One call: monthly cache lookup → API (on miss) → blob persist → optional CAL facts.

    Returns a unified envelope consumed by the veedcrawl plugin tool handlers.
    """
    cache_month = veedcrawl_cache.current_cache_month(tz_name)
    cache_key = veedcrawl_facts.build_cache_key(operation, request)
    env_norm = (env or "LIVE").strip().upper()
    if env_norm not in {"TEST", "LIVE"}:
        env_norm = "LIVE"

    hit: Optional[dict[str, Any]] = None
    if not force_refresh:
        hit = veedcrawl_cache.lookup(cache_month, cache_key, tz_name=tz_name)

    if hit is not None:
        veedcrawl_cache.log_fetch(
            cache_month=cache_month,
            cache_key=cache_key,
            operation=operation,
            cache_hit=True,
            env=env_norm,
            identity_id=identity_id,
        )
        envelope = {
            "ok": True,
            "operation": operation,
            "cache_month": cache_month,
            "cache_key": cache_key,
            "cache_hit": True,
            "api_calls": 0,
            "persisted": True,
            "blob_ref": hit.get("blob_ref"),
            "storage_ref": hit.get("storage_ref"),
            "identity_facts_written": False,
            "response": hit["response"],
        }
        if identity_id and write_cal_facts:
            envelope["identity_facts_written"] = _maybe_write_identity_facts(
                identity_id=identity_id,
                env=env_norm,
                operation=operation,
                response=hit["response"],
                cache_month=cache_month,
                cache_key=cache_key,
                blob_ref=hit.get("storage_ref") or hit.get("blob_ref"),
                handle=handle,
            )
        return envelope

    try:
        response = fetch_fn()
    except Exception as exc:
        log.warning("veedcrawl fetch failed operation=%s key=%s: %s", operation, cache_key, exc)
        return {
            "ok": False,
            "operation": operation,
            "cache_month": cache_month,
            "cache_key": cache_key,
            "cache_hit": False,
            "api_calls": 1,
            "persisted": False,
            "blob_ref": None,
            "storage_ref": None,
            "identity_facts_written": False,
            "error": str(exc),
            "response": None,
        }

    if not should_persist_response(operation, response):
        veedcrawl_cache.log_fetch(
            cache_month=cache_month,
            cache_key=cache_key,
            operation=operation,
            cache_hit=False,
            env=env_norm,
            identity_id=identity_id,
        )
        return {
            "ok": True,
            "operation": operation,
            "cache_month": cache_month,
            "cache_key": cache_key,
            "cache_hit": False,
            "api_calls": 1,
            "persisted": False,
            "blob_ref": None,
            "storage_ref": None,
            "identity_facts_written": False,
            "response": response,
            "pending_job": True,
        }

    storage_ref = veedcrawl_cache.store(cache_month, cache_key, operation, response)
    blob_ref = veedcrawl_cache.blob_ref_for(cache_month, cache_key)
    veedcrawl_cache.log_fetch(
        cache_month=cache_month,
        cache_key=cache_key,
        operation=operation,
        cache_hit=False,
        env=env_norm,
        identity_id=identity_id,
    )

    envelope: dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "cache_month": cache_month,
        "cache_key": cache_key,
        "cache_hit": False,
        "api_calls": 1,
        "persisted": True,
        "blob_ref": blob_ref,
        "storage_ref": storage_ref,
        "identity_facts_written": False,
        "response": response,
    }
    if identity_id and write_cal_facts:
        envelope["identity_facts_written"] = _maybe_write_identity_facts(
            identity_id=identity_id,
            env=env_norm,
            operation=operation,
            response=response,
            cache_month=cache_month,
            cache_key=cache_key,
            blob_ref=storage_ref,
            handle=handle,
        )
    return envelope


def _maybe_write_identity_facts(
    *,
    identity_id: int,
    env: str,
    operation: str,
    response: Any,
    cache_month: str,
    cache_key: str,
    blob_ref: Optional[str],
    handle: Optional[str],
) -> bool:
    facts = veedcrawl_facts.identity_facts_from_response(
        operation,
        response,
        cache_month=cache_month,
        cache_key=cache_key,
        blob_ref=blob_ref,
        handle=handle,
        at_iso=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if not facts:
        return False
    try:
        from . import cal  # noqa: WPS433 — bridge-local import

        n = cal.write_facts(
            identity_id=identity_id,
            campaign_id=None,
            namespace="identity",
            facts=facts,
            source="veedcrawl_persist",
            env=env,
        )
        return bool(n)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "veedcrawl CAL facts write failed identity=%s op=%s: %s",
            identity_id,
            operation,
            exc,
        )
        return False
