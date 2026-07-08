"""Risk page detection — scan IG pages for checkpoint/captcha/login-wall signals.

After each ``navigate``, RPA handlers call ``detect_risk(page_text)`` with
the snapshot text or body innerText. If a risk pattern is found, the
appropriate exception is raised. This enforces the skill's hard rule
(L982): checkpoint → stop run, never refresh/retry.

Patterns are intentionally broad to catch IG's various risk-page variants.
False positives on legitimate profile text are unlikely because the
patterns are distinct phrases that don't appear in normal profile bios.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import (  # noqa: E402
    CheckpointError,
    RateLimitedError,
    SessionExpiredError,
)

# Checkpoint / captcha / "suspicious activity" — hard stop, never retry
_CHECKPOINT_PATTERNS = (
    re.compile(r"challenge", re.IGNORECASE),
    re.compile(r"checkpoint", re.IGNORECASE),
    re.compile(r"captcha", re.IGNORECASE),
    re.compile(r"confirm you'?re human", re.IGNORECASE),
    re.compile(r"action blocked", re.IGNORECASE),
    re.compile(r"suspicious activity", re.IGNORECASE),
)

# Rate limit — "try again later" style; switch surface, don't retry same handle
_RATE_LIMIT_PATTERNS = (
    re.compile(r"try again later", re.IGNORECASE),
    re.compile(r"please wait a few minutes", re.IGNORECASE),
)

# Login wall — session expired; operator must re-login debug Chrome
_LOGIN_WALL_PATTERNS = (
    re.compile(r"log in to instagram", re.IGNORECASE),
    re.compile(r"sign up to see", re.IGNORECASE),
    re.compile(r"enter your.*password", re.IGNORECASE),
)

# Empty render — grid didn't load (transient, retryable once)
_EMPTY_SIGNALS = (
    "no posts yet",
    "this account is private",
)


def detect_risk(page_text: str) -> str | None:
    """Scan page text for risk signals. Returns risk code or ``None``.

    Args:
        page_text: The ``innerText`` or snapshot text from the navigated page.

    Returns:
        One of ``"checkpoint"``, ``"rate_limited"``, ``"session_expired"``,
        ``"empty_render"``, or ``None`` if no risk detected.
    """
    if not page_text:
        return "empty_render"
    text_lower = page_text.lower()

    for pat in _CHECKPOINT_PATTERNS:
        if pat.search(text_lower):
            return "checkpoint"
    for pat in _RATE_LIMIT_PATTERNS:
        if pat.search(text_lower):
            return "rate_limited"
    for pat in _LOGIN_WALL_PATTERNS:
        if pat.search(text_lower):
            return "session_expired"

    # Empty render: very short text with no IG-specific markers
    # Only trigger for extremely short text (< 20 chars) to avoid false positives
    # on legitimate short profile pages
    if len(text_lower.strip()) < 20 and not any(
        marker in text_lower for marker in ("followers", "following", "posts")
    ):
        return "empty_render"

    return None


def raise_on_risk(page_text: str) -> None:
    """Scan page text and raise the appropriate exception if risk detected.

    Args:
        page_text: The ``innerText`` or snapshot text from the navigated page.

    Raises:
        CheckpointError: If checkpoint/captcha detected.
        RateLimitedError: If rate-limit signal detected.
        SessionExpiredError: If login wall detected.
    """
    risk = detect_risk(page_text)
    if risk == "checkpoint":
        raise CheckpointError()
    if risk == "rate_limited":
        raise RateLimitedError()
    if risk == "session_expired":
        raise SessionExpiredError()
    # empty_render is NOT raised here — callers handle it separately
