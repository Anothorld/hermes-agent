"""Async ``POST /v1/search`` job orchestration.

Veedcrawl retired the synchronous ``GET /v1/search`` route. Search now submits
a JSON body (``query``, ``platforms``, ``limit``), polls ``GET /v1/search/{jobId}``,
and normalizes ``result.results`` into the flat list shape callers expect.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from plugins.veedcrawl._internal import cache, poller
from plugins.veedcrawl._internal.errors import (
    VeedcrawlAPIError,
    VeedcrawlJobFailedError,
    VeedcrawlJobTimeoutError,
)

_DEFAULT_SEARCH_TIMEOUT_S = 180.0
_CACHE_TTL_SEARCH = 24 * 3600
# Docs: ~5–15 credits per platform; use per-platform ceiling before submit.
_COST_SEARCH_PER_PLATFORM = 15

_PLATFORM_ALIASES = {
    "ig": "instagram",
    "tt": "tiktok",
    "yt": "youtube",
}


def _normalize_platform(platform: Optional[str]) -> Optional[str]:
    if not platform:
        return None
    lc = str(platform).strip().lower()
    return _PLATFORM_ALIASES.get(lc, lc)


def build_submit_body(*, q: str, platform: Optional[str], limit: int) -> dict[str, Any]:
    """Map agent ``q`` / ``platform`` args to the REST submit body."""
    limit = max(1, min(int(limit), 20))
    body: dict[str, Any] = {"query": q.strip(), "limit": limit}
    plat = _normalize_platform(platform)
    if plat:
        body["platforms"] = [plat]
    else:
        body["platforms"] = ["tiktok", "instagram", "youtube"]
    return body


def normalize_search_items(payload: Any) -> list[dict[str, Any]]:
    """Extract video hits from async job payloads or legacy sync arrays."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []

    result = payload.get("result")
    if isinstance(result, dict):
        nested = result.get("results")
        if isinstance(nested, list):
            return [x for x in nested if isinstance(x, dict)]

    direct = payload.get("results")
    if isinstance(direct, list):
        return [x for x in direct if isinstance(x, dict)]
    return []


def run_search(
    *,
    q: str,
    platform: Optional[str],
    limit: int,
    force_refresh: bool,
    timeout_s: float = _DEFAULT_SEARCH_TIMEOUT_S,
    request: Callable[..., tuple[Any, Mapping[str, str]]],
    ensure_credits: Callable[[int], None],
    sleep: Callable[[float], None],
) -> list[dict[str, Any]]:
    """Submit, poll, and return normalized search hits."""
    query = (q or "").strip()
    if not query:
        raise VeedcrawlAPIError("search requires non-empty q", status_code=400)

    plat = _normalize_platform(platform)
    limit = max(1, min(int(limit), 20))
    cache_key = {"q": query, "platform": plat, "limit": limit}
    if not force_refresh:
        cached = cache.get("search", cache_key)
        if isinstance(cached, list):
            return cached

    body = build_submit_body(q=query, platform=platform, limit=limit)
    platforms = body["platforms"]
    ensure_credits(max(_COST_SEARCH_PER_PLATFORM, len(platforms) * _COST_SEARCH_PER_PLATFORM))

    submit_payload, _ = request("POST", "/v1/search", json_body=body)
    if not isinstance(submit_payload, dict):
        raise VeedcrawlAPIError(
            f"search submit returned unexpected payload: {submit_payload!r}",
            status_code=500,
        )
    job_id = str(submit_payload.get("jobId") or "")
    if not job_id:
        raise VeedcrawlAPIError(
            f"search submit returned no jobId: {submit_payload!r}",
            status_code=500,
        )

    estimated = submit_payload.get("estimatedCredits")
    if isinstance(estimated, (int, float)) and int(estimated) > 0:
        ensure_credits(int(estimated))

    def _fetch() -> dict[str, Any]:
        payload, _ = request("GET", f"/v1/search/{job_id}")
        return payload if isinstance(payload, dict) else {}

    try:
        final = poller.poll(_fetch, timeout_s=timeout_s, sleep=sleep)
    except TimeoutError as exc:
        raise VeedcrawlJobTimeoutError(str(exc), job_id=job_id, timeout_s=timeout_s) from exc

    status = str(final.get("status") or "").lower()
    if status == "failed":
        err = final.get("error") or {}
        message = (
            str(err.get("message") or err)
            if isinstance(err, dict)
            else str(err or "search job failed")
        )
        raise VeedcrawlJobFailedError(
            message,
            job_id=job_id,
            job_error=err if isinstance(err, dict) else {"message": message},
        )
    if status != "completed":
        raise VeedcrawlAPIError(
            f"search job ended with status {status!r}",
            status_code=500,
        )

    items = normalize_search_items(final)
    if not items:
        results_payload, _ = request("GET", f"/v1/search/{job_id}/results")
        items = normalize_search_items(results_payload)

    cache.put("search", cache_key, items, ttl_s=_CACHE_TTL_SEARCH)
    return items
