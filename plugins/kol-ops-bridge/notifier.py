"""DingTalk notifier for human-in-the-loop deep-links.

Given an event (escalation opened, approval pending, draft ready),
formats a DingTalk markdown message with a deep-link back to the
operator console and posts it via the configured webhook.

Design:
- Stateless. No DB writes. Just HTTP POST.
- Webhook URL + secret read from env vars (HERMES_DINGTALK_WEBHOOK,
  HERMES_DINGTALK_SECRET); MUST NOT be hardcoded.
- Failure-tolerant: webhook errors are logged but never raise — a
  failed notification must NOT block a fact write or escalation.
- Optional: when webhook unset, returns {"sent": false, "reason":
  "webhook_not_configured"} silently (TEST environments).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "HERMES_DINGTALK_WEBHOOK"
SECRET_ENV = "HERMES_DINGTALK_SECRET"
CONSOLE_BASE_ENV = "HERMES_KOL_CONSOLE_BASE_URL"

DEFAULT_TIMEOUT_S = 5


class NotifierError(Exception):
    """Raised only for programmer error (e.g. malformed payload)."""


def _sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return urllib.parse.quote_plus(base64.b64encode(hmac_code))


def _signed_url(webhook: str, secret: Optional[str]) -> str:
    if not secret:
        return webhook
    timestamp = str(round(time.time() * 1000))
    sign = _sign(secret, timestamp)
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={timestamp}&sign={sign}"


def _console_link(kind: str, ref: Mapping[str, Any]) -> Optional[str]:
    base = os.environ.get(CONSOLE_BASE_ENV)
    if not base:
        return None
    base = base.rstrip("/")
    if kind == "escalation":
        return f"{base}/escalations/{ref.get('escalation_id', '')}"
    if kind == "approval":
        return f"{base}/approvals/{ref.get('approval_id', '')}"
    if kind == "draft_ready":
        return (
            f"{base}/identities/{ref.get('identity_id', '')}"
            f"?campaign={ref.get('campaign_id', '')}"
        )
    return base


def _build_markdown(kind: str, title: str, lines: list[str], link: Optional[str]) -> dict:
    body = "\n\n".join(lines)
    if link:
        body += f"\n\n[Open in console]({link})"
    return {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": body},
    }


def _post(webhook: str, payload: dict, *, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def notify(
    *,
    kind: str,
    title: str,
    lines: list[str],
    ref: Optional[Mapping[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """Send a DingTalk markdown notification.

    Args:
        kind: One of {"escalation", "approval", "draft_ready", "info"}.
        title: Short title (used by DingTalk preview).
        lines: List of markdown lines for the body.
        ref: Reference dict for deep-link construction (e.g.
            {"escalation_id": 42}).
        timeout: HTTP timeout in seconds.

    Returns:
        Dict with at least `{"sent": bool}`. Never raises on transport
        failure.
    """
    if kind not in {"escalation", "approval", "draft_ready", "info"}:
        raise NotifierError(f"unknown kind: {kind}")
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        raise NotifierError("lines must be list[str]")

    webhook = os.environ.get(WEBHOOK_ENV)
    if not webhook:
        return {"sent": False, "reason": "webhook_not_configured"}

    secret = os.environ.get(SECRET_ENV)
    link = _console_link(kind, ref or {})
    payload = _build_markdown(kind, title, lines, link)

    try:
        url = _signed_url(webhook, secret)
        result = _post(url, payload, timeout=timeout)
        ok = result.get("errcode", 0) == 0 if isinstance(result, dict) else False
        if not ok:
            logger.warning("dingtalk notify non-ok response: %s", result)
        return {"sent": ok, "response": result}
    except Exception as exc:  # pragma: no cover - transport failure path
        logger.warning("dingtalk notify failed: %s", exc)
        return {"sent": False, "reason": "transport_error", "error": str(exc)}
