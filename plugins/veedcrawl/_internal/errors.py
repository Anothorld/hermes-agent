"""Veedcrawl error hierarchy.

The error classes here mirror the documented Veedcrawl error surface
(https://docs.veedcrawl.com/reference/errors) so that ``tools.py`` can map
them directly to ``tool_error(..., code=...)`` payloads without leaking
``httpx`` types into the public boundary.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class VeedcrawlError(RuntimeError):
    """Base class for all Veedcrawl plugin errors."""

    code: str = "veedcrawl_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details)

    def to_payload(self) -> dict[str, Any]:
        """Return a ``tool_error``-friendly extras dict."""
        return {"code": self.code, **self.details}


class VeedcrawlAuthRequiredError(VeedcrawlError):
    """Raised when the API key is missing or rejected (HTTP 401)."""

    code = "auth"


class VeedcrawlInsufficientCreditsError(VeedcrawlError):
    """Raised when the credit guardrail trips or the API returns 403 for credits."""

    code = "insufficient_credits"


class VeedcrawlRateLimitError(VeedcrawlError):
    """Raised after a second consecutive 429 (one retry already attempted)."""

    code = "rate_limited"


class VeedcrawlJobFailedError(VeedcrawlError):
    """Raised when an async job ends with ``status="failed"``."""

    code = "job_failed"

    def __init__(
        self,
        message: str,
        *,
        job_id: str,
        job_error: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, job_id=job_id, job_error=dict(job_error or {}))


class VeedcrawlJobTimeoutError(VeedcrawlError):
    """Raised when polling exceeds ``timeout_s``."""

    code = "job_timeout"


class VeedcrawlAPIError(VeedcrawlError):
    """Generic 4xx / 5xx fall-through after retries."""

    code = "api_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.status_code = status_code
