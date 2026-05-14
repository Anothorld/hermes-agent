"""Thin HTTP transport for Meta Graph API ``/creator_marketplace/*`` endpoints.

Single responsibility: build the URL, inject the access token, parse Graph
API errors into typed exceptions. No business logic lives here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .credentials import FBCredentialProvider
from .errors import FBCreatorAPIError, FBCreatorAuthRequiredError

# Graph API error codes that mean "credentials are no longer valid".
# 190 = OAuth access token issues; 102 = session expired.
AUTH_ERROR_CODES = frozenset({190, 102})

# Graph API error codes that indicate rate limiting.
# 4 = Application request limit; 17 = User request limit; 32 = Page-level limit.
RATE_LIMIT_ERROR_CODES = frozenset({4, 17, 32})

DEFAULT_BASE = "https://graph.facebook.com"
DEFAULT_TIMEOUT = 30.0


class FBGraphHTTPClient:
    """HTTP client for Meta Graph API.

    Parameters
    ----------
    credentials:
        Credential provider (DIP — depend on the abstraction).
    http:
        Optional pre-configured ``httpx.Client`` (useful for tests with
        ``MockTransport``).
    base_url:
        Override the Graph base URL (defaults to ``https://graph.facebook.com``).
    """

    def __init__(
        self,
        credentials: FBCredentialProvider,
        *,
        http: Optional[httpx.Client] = None,
        base_url: str = DEFAULT_BASE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._credentials = credentials
        self._http = http
        self._owns_http = http is None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # -- public API -------------------------------------------------------

    def get(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params)

    def close(self) -> None:
        if self._owns_http and self._http is not None:
            self._http.close()
            self._http = None

    # -- internals --------------------------------------------------------

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        creds = self._credentials.resolve()
        url = f"{self._base_url}/{creds.api_version}{_normalize_path(path)}"
        merged_params: Dict[str, Any] = {
            k: v for k, v in (params or {}).items() if v is not None and v != ""
        }
        merged_params["access_token"] = creds.page_access_token

        try:
            response = self._client().request(method, url, params=merged_params)
        except httpx.HTTPError as exc:
            raise FBCreatorAPIError(
                f"Graph API HTTP transport error for {method} {path}: {exc}"
            ) from exc

        if response.status_code >= 400:
            self._raise_for_error(response, method=method, path=path)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise FBCreatorAPIError(
                f"Graph API returned non-JSON body for {method} {path}: {exc}",
                status_code=response.status_code,
            ) from exc

    def _raise_for_error(
        self, response: httpx.Response, *, method: str, path: str
    ) -> None:
        body: dict = {}
        try:
            body = response.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}

        code = _safe_int(err.get("code"))
        subcode = _safe_int(err.get("error_subcode"))
        trace_id = err.get("fbtrace_id")
        message = err.get("message") or response.reason_phrase or "Graph API error"
        retry_after = response.headers.get("Retry-After")

        # Auth failure → distinct exception type so the tool layer can return a
        # specific "please reconfigure your token" hint.
        if response.status_code == 401 or (code is not None and code in AUTH_ERROR_CODES):
            raise FBCreatorAuthRequiredError(
                f"Graph API rejected the Page Access Token "
                f"({method} {path}, code={code}, subcode={subcode}): {message}"
            )

        if code is not None and code in RATE_LIMIT_ERROR_CODES:
            hint = f" Retry-After={retry_after}s." if retry_after else ""
            raise FBCreatorAPIError(
                f"Graph API rate limit hit on {method} {path} "
                f"(code={code}): {message}.{hint}",
                status_code=response.status_code,
                fb_error_code=code,
                fb_error_subcode=subcode,
                fb_trace_id=trace_id,
                retry_after=retry_after,
            )

        raise FBCreatorAPIError(
            f"Graph API error on {method} {path} "
            f"(http={response.status_code}, code={code}): {message}",
            status_code=response.status_code,
            fb_error_code=code,
            fb_error_subcode=subcode,
            fb_trace_id=trace_id,
            retry_after=retry_after,
        )


def _normalize_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return path


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
