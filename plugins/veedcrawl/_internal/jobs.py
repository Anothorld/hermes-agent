"""Async-job orchestration for the Veedcrawl client.

Kept separate from ``client.py`` so the public client stays under the
project's 250-line file budget. ``run_async_job`` and ``finalise_job`` know
about Veedcrawl's submit/poll contract and the cache key shape, but rely on
the caller for HTTP transport, credit guardrail, and JSON decoding.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from plugins.veedcrawl._internal import cache, poller, rate_limit
from plugins.veedcrawl._internal.errors import (
    VeedcrawlAPIError,
    VeedcrawlJobFailedError,
    VeedcrawlJobTimeoutError,
)

# Permanent cache: completed job payloads are deterministic per Veedcrawl docs.
_CACHE_TTL_JOB_COMPLETED: Optional[float] = None


def _strip_private(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys starting with ``_`` so caches stay clean across versions."""
    return {k: v for k, v in payload.items() if not str(k).startswith("_")}


def finalise_job(
    *,
    endpoint: str,
    cache_params: dict[str, Any],
    payload: dict[str, Any],
    headers: Mapping[str, str],
    cost: int,
) -> dict[str, Any]:
    """Convert a Veedcrawl job payload into the agent-facing result dict."""
    status = str(payload.get("status") or "").lower()
    if status == "failed":
        err = payload.get("error") or {}
        raise VeedcrawlJobFailedError(
            str(err.get("message") or "veedcrawl job failed"),
            job_id=str(payload.get("jobId") or ""),
            job_error=err if isinstance(err, dict) else {"message": str(err)},
        )
    if status != "completed":
        # Resumed job still running — return as-is so caller can poll later.
        return {
            "job_id": str(payload.get("jobId") or ""),
            "status": status or "unknown",
            "_rate_limit_remaining": rate_limit.parse_remaining(headers),
            "_cached": False,
        }

    finalised = {
        "job_id": str(payload.get("jobId") or ""),
        "status": "completed",
        "result_json": payload.get("resultJson"),
        "credits_used": cost,
        "_rate_limit_remaining": rate_limit.parse_remaining(headers),
        "_cached": False,
    }
    cache.put(endpoint, cache_params, _strip_private(finalised), ttl_s=_CACHE_TTL_JOB_COMPLETED)
    return finalised


def run_async_job(
    *,
    endpoint: str,
    submit_path: str,
    poll_path_prefix: str,
    cost: int,
    cache_params: dict[str, Any],
    request_body: dict[str, Any],
    wait: bool,
    timeout_s: float,
    force_refresh: bool,
    job_id: Optional[str],
    request: Callable[..., tuple[dict[str, Any], Mapping[str, str]]],
    ensure_credits: Callable[[int], None],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Submit + poll a Veedcrawl async job. See ``client.VeedcrawlClient``."""
    # Cache hit short-circuits everything.
    if not force_refresh and not job_id:
        cached = cache.get(endpoint, cache_params)
        if cached is not None:
            cached["_cached"] = True
            return cached

    # Resume an existing job (escape hatch for wait=False workflows).
    if job_id:
        payload, headers = request("GET", f"{poll_path_prefix}{job_id}")
        return finalise_job(
            endpoint=endpoint,
            cache_params=cache_params,
            payload=payload,
            headers=headers,
            cost=cost,
        )

    # Guard credits before submitting (paid call).
    ensure_credits(cost)

    body = {k: v for k, v in request_body.items() if v is not None}
    submit_payload, submit_headers = request("POST", submit_path, json_body=body)
    new_job_id = str(submit_payload.get("jobId") or "")
    if not new_job_id:
        raise VeedcrawlAPIError(
            f"{endpoint} submit returned no jobId: {submit_payload!r}",
            status_code=500,
        )

    if not wait:
        return {
            "job_id": new_job_id,
            "status": str(submit_payload.get("status") or "queued"),
            "_rate_limit_remaining": rate_limit.parse_remaining(submit_headers),
            "_cached": False,
        }

    def _fetch() -> dict[str, Any]:
        payload, _ = request("GET", f"{poll_path_prefix}{new_job_id}")
        return payload

    try:
        final = poller.poll(_fetch, timeout_s=timeout_s, sleep=sleep)
    except TimeoutError as exc:
        raise VeedcrawlJobTimeoutError(
            str(exc), job_id=new_job_id, timeout_s=timeout_s,
        ) from exc

    # Re-fetch headers so the caller sees a fresh remaining count.
    _, final_headers = request("GET", f"{poll_path_prefix}{new_job_id}")
    return finalise_job(
        endpoint=endpoint,
        cache_params=cache_params,
        payload=final,
        headers=final_headers,
        cost=cost,
    )
