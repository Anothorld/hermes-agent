"""Custom exceptions for kol-discovery-rpa.

All handlers catch these and convert to structured ``tool_error`` responses
with machine-readable error codes. ``RpaError`` is the base; specific
subclasses map to distinct failure modes the agent should handle differently.
"""

from __future__ import annotations


class RpaError(Exception):
    """Base error for RPA tool failures.

    Attributes:
        code: Machine-readable error code (e.g. ``dom_changed``).
        detail: Human-readable detail string.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(f"{code}: {self.detail}")


class DomChangedError(RpaError):
    """IG DOM structure changed — JS selectors no longer match.

    Agent should fall back to ``browser_*`` for this URL once.
    """

    def __init__(self, detail: str = "IG DOM structure changed") -> None:
        super().__init__("dom_changed", detail)


class RateLimitedError(RpaError):
    """IG returned a rate-limit / "try again later" signal.

    Agent should switch surface (hashtag → google → nox) and not retry
    the same handle this run.
    """

    def __init__(self, detail: str = "rate limited by IG") -> None:
        super().__init__("rate_limited", detail)


class CheckpointError(RpaError):
    """IG checkpoint / captcha / "suspicious activity" page detected.

    Agent must STOP the run immediately (``mode_gate_blocked: rate_limited``).
    Do not refresh or retry.
    """

    def __init__(self, detail: str = "checkpoint/captcha detected") -> None:
        super().__init__("checkpoint", detail)


class SessionExpiredError(RpaError):
    """IG session cookie expired — login wall detected.

    Operator needs to re-login in the debug Chrome profile.
    """

    def __init__(self, detail: str = "session expired, re-login required") -> None:
        super().__init__("session_expired", detail)


class DownloadError(RpaError):
    """yt-dlp video download failed (IG anti-scrape, timeout, disk cap)."""

    def __init__(self, code: str = "download_error", detail: str = "") -> None:
        super().__init__(code, detail or code)


class QuotaExceededError(RpaError):
    """Per-run profile or reel page quota exhausted (40 profile / 200 reel)."""

    def __init__(self, detail: str = "per-run quota exceeded") -> None:
        super().__init__("quota_exceeded", detail)


class CookieExpiredError(DownloadError):
    """sessionid cookie missing or expired — yt-dlp cannot authenticate."""

    def __init__(self, detail: str = "cookie expired, re-login Chrome") -> None:
        super().__init__("cookie_expired", detail)
