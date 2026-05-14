"""Veedcrawl API client (public surface).

Sync ``httpx``-based wrapper around https://api.veedcrawl.com. Responsible for:

* resolving the API key (env var only — no ``hermes_cli`` coupling for v1)
* attaching the ``x-api-key`` header
* retrying once on 429 after honouring ``X-RateLimit-Reset``
* caching idempotent responses (``metadata`` / ``profile`` / completed jobs)
* enforcing the credit guardrail before paid calls
* polling async jobs to completion via ``_internal.jobs``

HTTP-error mapping lives in ``_internal.http``; async-job orchestration lives
in ``_internal.jobs`` — this file owns only the coordinating client class.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

import httpx

from plugins.veedcrawl._internal import cache, http as _http, jobs, rate_limit
from plugins.veedcrawl._internal.errors import (
    VeedcrawlAPIError,
    VeedcrawlAuthRequiredError,
    VeedcrawlInsufficientCreditsError,
)

DEFAULT_BASE_URL = "https://api.veedcrawl.com"
_API_KEY_ENV_VARS: tuple[str, ...] = ("VEEDCRAWL_API_KEY", "X_API_KEY")
_ME_CACHE_TTL_S = 60.0
_DEFAULT_SAFETY_FACTOR = 2.0
_DEFAULT_TIMEOUT_S = 180.0
_DEFAULT_HTTP_TIMEOUT_S = 30.0

# Credit cost table (must match README + Veedcrawl docs).
_COST_TRANSCRIPT_NATIVE = 1
_COST_TRANSCRIPT_GENERATE = 5
_COST_EXTRACT = 10

_CACHE_TTL_METADATA = 24 * 3600
_CACHE_TTL_PROFILE = 6 * 3600

# (poll_path_prefix, cost) for each async endpoint that supports lookup-by-id.
_JOB_LOOKUP_SPECS: dict[str, tuple[str, int]] = {
    "transcript": ("/v1/transcript/", _COST_TRANSCRIPT_GENERATE),
    "extract": ("/v1/extract/", _COST_EXTRACT),
}


def resolve_api_key() -> Optional[str]:
    """Return the configured API key from env, or ``None`` if unset."""
    for name in _API_KEY_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


class VeedcrawlClient:
    """Thin synchronous wrapper around the Veedcrawl REST API."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = rate_limit.real_sleep,
        now: Callable[[], float] = time.time,
        safety_factor: float = _DEFAULT_SAFETY_FACTOR,
    ) -> None:
        key = api_key if api_key is not None else resolve_api_key()
        if not key:
            raise VeedcrawlAuthRequiredError(
                "veedcrawl auth required: set VEEDCRAWL_API_KEY (or X_API_KEY) "
                "to a key from https://veedcrawl.com/login"
            )
        self._api_key = key
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._now = now
        self._safety_factor = max(1.0, float(safety_factor))
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={"x-api-key": self._api_key},
            transport=transport,
            timeout=_DEFAULT_HTTP_TIMEOUT_S,
        )
        self._me_cache: Optional[tuple[float, dict[str, Any]]] = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "VeedcrawlClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(  # HTTP layer

        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        _retried_429: bool = False,
    ) -> tuple[dict[str, Any], httpx.Headers]:
        try:
            response = self._http.request(method, path, params=params, json=json_body)
        except httpx.TransportError as exc:
            raise VeedcrawlAPIError(
                f"network error contacting Veedcrawl: {exc}",
                status_code=0,
            ) from exc

        if response.status_code == 429 and not _retried_429:
            rate_limit.sleep_until_reset(
                response.headers, now=self._now(), sleep=self._sleep,
            )
            return self._request(
                method, path, params=params, json_body=json_body, _retried_429=True,
            )

        if response.status_code >= 400:
            _http.raise_for_status(response)

        return _http.decode_response(response), response.headers

    def me(self, *, force: bool = False) -> dict[str, Any]:
        """Return the org/credits payload, cached for ``_ME_CACHE_TTL_S``."""
        if not force and self._me_cache is not None:
            stored_at, payload = self._me_cache
            if (self._now() - stored_at) < _ME_CACHE_TTL_S:
                return payload
        payload, _ = self._request("GET", "/v1/me")
        self._me_cache = (self._now(), payload)
        return payload

    def health(self) -> dict[str, Any]:
        payload, _ = self._request("GET", "/health")
        return payload or {"status": "ok"}

    def _ensure_credits(self, needed: int) -> None:
        """Assert ``creditsRemaining >= needed × safety``; raise otherwise."""
        info = self.me()
        remaining = int(info.get("creditsRemaining", 0))
        threshold = int(needed * self._safety_factor)
        if remaining < threshold:
            raise VeedcrawlInsufficientCreditsError(
                f"need {threshold} credits (cost {needed} × safety "
                f"{self._safety_factor:g}) but {remaining} remaining; top up at "
                "https://veedcrawl.com",
                credits_remaining=remaining,
                credits_needed=needed,
                safety_factor=self._safety_factor,
            )

    def metadata(self, url: str, *, force_refresh: bool = False) -> dict[str, Any]:
        params = {"url": url}
        if not force_refresh:
            cached = cache.get("metadata", params)
            if cached is not None:
                cached["_cached"] = True
                return cached
        payload, headers = self._request("GET", "/v1/metadata", params=params)
        payload["_rate_limit_remaining"] = rate_limit.parse_remaining(headers)
        payload["_cached"] = False
        cache.put("metadata", params, _strip_private(payload), ttl_s=_CACHE_TTL_METADATA)
        return payload

    def profile(  # /v1/{ig,tt}/profile

        self,
        *,
        platform: str,
        username: Optional[str],
        url: Optional[str],
        limit: int,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        platform_lc = (platform or "").lower()
        if platform_lc not in {"instagram", "tiktok"}:
            raise VeedcrawlAPIError(
                f"unsupported platform {platform!r}; expected 'instagram' or 'tiktok'",
                status_code=400,
            )
        if not (username or url):
            raise VeedcrawlAPIError(
                "must provide either 'username' or 'url'", status_code=400,
            )
        params: dict[str, Any] = {"limit": limit}
        if username:
            params["username"] = username.lstrip("@")
        if url:
            params["url"] = url
        cache_key = {"platform": platform_lc, **params}
        if not force_refresh:
            cached = cache.get("profile", cache_key)
            if cached is not None:
                cached["_cached"] = True
                return cached
        payload, headers = self._request("GET", f"/v1/{platform_lc}/profile", params=params)
        payload["_rate_limit_remaining"] = rate_limit.parse_remaining(headers)
        payload["_cached"] = False
        cache.put("profile", cache_key, _strip_private(payload), ttl_s=_CACHE_TTL_PROFILE)
        return payload

    def transcript(  # async job

        self,
        *,
        url: str,
        mode: str = "auto",
        lang: Optional[str] = None,
        wait: bool = True,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        force_refresh: bool = False,
        job_id: Optional[str] = None,
    ) -> dict[str, Any]:
        cost = _COST_TRANSCRIPT_NATIVE if mode == "native" else _COST_TRANSCRIPT_GENERATE
        return jobs.run_async_job(
            endpoint="transcript",
            submit_path="/v1/transcript",
            poll_path_prefix="/v1/transcript/",
            cost=cost,
            cache_params={"url": url, "mode": mode, "lang": lang},
            request_body={"url": url, "mode": mode, "lang": lang},
            wait=wait,
            timeout_s=timeout_s,
            force_refresh=force_refresh,
            job_id=job_id,
            request=self._request,
            ensure_credits=self._ensure_credits,
            sleep=self._sleep,
        )

    def extract(
        self,
        *,
        url: str,
        prompt: str,
        schema: Optional[dict[str, Any]] = None,
        lang: Optional[str] = None,
        wait: bool = True,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        force_refresh: bool = False,
        job_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return jobs.run_async_job(
            endpoint="extract",
            submit_path="/v1/extract",
            poll_path_prefix="/v1/extract/",
            cost=_COST_EXTRACT,
            cache_params={"url": url, "prompt": prompt, "schema": schema, "lang": lang},
            request_body={"url": url, "prompt": prompt, "schema": schema, "lang": lang},
            wait=wait,
            timeout_s=timeout_s,
            force_refresh=force_refresh,
            job_id=job_id,
            request=self._request,
            ensure_credits=self._ensure_credits,
            sleep=self._sleep,
        )

    # ---- async-job recovery -------------------------------------------------

    def lookup_job(self, *, endpoint: str, job_id: str) -> dict[str, Any]:
        """Fetch the result of an existing async job by id.

        Use this when an earlier ``transcript`` / ``extract`` call returned a
        ``job_id`` (e.g. with ``wait=False`` or because the agent dropped the
        original payload).  Does not spend credits — it is a plain ``GET``.
        """
        endpoint_lc = (endpoint or "").lower()
        spec = _JOB_LOOKUP_SPECS.get(endpoint_lc)
        if spec is None:
            raise VeedcrawlAPIError(
                f"unsupported endpoint {endpoint!r}; expected one of "
                f"{sorted(_JOB_LOOKUP_SPECS)}",
                status_code=400,
            )
        if not job_id:
            raise VeedcrawlAPIError("job_id is required", status_code=400)
        poll_prefix, cost = spec
        payload, headers = self._request("GET", f"{poll_prefix}{job_id}")
        return jobs.finalise_job(
            endpoint=endpoint_lc,
            cache_params={"job_id": job_id},
            payload=payload,
            headers=headers,
            cost=cost,
        )


def _strip_private(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if not str(k).startswith("_")}
