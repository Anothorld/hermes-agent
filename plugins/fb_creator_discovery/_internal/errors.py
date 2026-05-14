"""Typed errors for the Facebook Creator Discovery plugin.

All exceptions inherit from :class:`FBCreatorError` so callers can catch a
single base class. Specific subclasses carry structured fields useful for
agents (status code, Graph API error code/subcode, fbtrace_id).
"""

from __future__ import annotations

from typing import Optional


class FBCreatorError(RuntimeError):
    """Base error raised by the FB Creator Discovery plugin."""


class FBCreatorAuthRequiredError(FBCreatorError):
    """Raised when no usable Page Access Token is available, or the token
    has expired / been revoked. The handler maps this to a friendly tool
    error that instructs the user how to configure credentials.
    """


class FBCreatorAPIError(FBCreatorError):
    """Structured Graph API failure.

    Attributes
    ----------
    status_code:
        HTTP status code from Graph API.
    fb_error_code / fb_error_subcode:
        Graph API ``error.code`` / ``error.subcode`` (see Meta docs).
    fb_trace_id:
        ``error.fbtrace_id`` returned by Graph API; useful for support tickets.
    retry_after:
        Optional ``Retry-After`` header value (seconds, as string).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        fb_error_code: Optional[int] = None,
        fb_error_subcode: Optional[int] = None,
        fb_trace_id: Optional[str] = None,
        retry_after: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.fb_error_code = fb_error_code
        self.fb_error_subcode = fb_error_subcode
        self.fb_trace_id = fb_trace_id
        self.retry_after = retry_after
