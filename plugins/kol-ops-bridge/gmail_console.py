"""Fetch per-operator Gmail connections from KOL Ops Console internal API."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from .gmail_client import GmailClient

log = logging.getLogger(__name__)

_MULTI_OPERATOR_CACHE: tuple[float, bool] | None = None
_CONNECTION_CACHE_TTL_SEC = float(os.environ.get("KOC_GMAIL_CONNECTION_CACHE_SEC", "60"))


@dataclass(frozen=True)
class OperatorMailbox:
    user_id: int
    google_email: str
    client: GmailClient


def _console_base() -> str:
    return os.environ.get(
        "KOC_CONSOLE_BASE",
        "http://127.0.0.1:8000",
    ).rstrip("/")


def _internal_headers() -> dict[str, str]:
    key = (
        os.environ.get("KOC_INTERNAL_API_KEY")
        or os.environ.get("HERMES_KOL_OPS_BRIDGE_KEY")
        or os.environ.get("KOC_BRIDGE_KEY")
        or ""
    ).strip()
    hdrs = {"Accept": "application/json"}
    if key:
        hdrs["X-Internal-Key"] = key
    return hdrs


def fetch_gmail_connections() -> list[dict[str, Any]]:
    """Return active connections from Console ``GET /internal/gmail-connections``."""
    url = f"{_console_base()}/internal/gmail-connections"
    req = urllib.request.Request(url, headers=_internal_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        log.warning("console gmail-connections fetch failed: %s", exc)
        return []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return []
    items = payload.get("connections") if isinstance(payload, dict) else None
    return list(items) if isinstance(items, list) else []


def invalidate_gmail_connection_cache() -> None:
    """Clear cached multi-operator detection (call after OAuth connect/disconnect)."""
    global _MULTI_OPERATOR_CACHE
    _MULTI_OPERATOR_CACHE = None


def multi_operator_gmail_enabled() -> bool:
    """True when Console reports at least one active operator Gmail connection."""
    global _MULTI_OPERATOR_CACHE
    now = time.monotonic()
    if (
        _MULTI_OPERATOR_CACHE is not None
        and now - _MULTI_OPERATOR_CACHE[0] < _CONNECTION_CACHE_TTL_SEC
    ):
        return _MULTI_OPERATOR_CACHE[1]
    enabled = len(fetch_gmail_connections()) > 0
    _MULTI_OPERATOR_CACHE = (now, enabled)
    return enabled


def resolve_console_user_id(*, email: str | None = None, user_id: int | None = None) -> Optional[int]:
    """Resolve Console ``users.id`` via internal API (for CLI/gateway approve)."""
    params: list[str] = []
    if user_id is not None and user_id > 0:
        params.append(f"user_id={int(user_id)}")
    if email:
        params.append(f"email={quote(email.strip().lower())}")
    if not params:
        return None
    url = f"{_console_base()}/internal/users/resolve?{'&'.join(params)}"
    req = urllib.request.Request(url, headers=_internal_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        log.warning("console user resolve failed: %s", exc)
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("user_id") is not None:
        return int(payload["user_id"])
    return None


def default_operator_user_id() -> Optional[int]:
    """Env fallback for unattended gateway/CLI approve (``KOC_DEFAULT_OPERATOR_USER_ID``)."""
    raw = os.environ.get("KOC_DEFAULT_OPERATOR_USER_ID", "").strip()
    if not raw:
        return None
    try:
        uid = int(raw)
    except ValueError:
        return None
    return uid if uid > 0 else None


def refresh_token_file_from_console(user_id: int) -> Path | None:
    """Ask Console to materialize ``gmail_tokens/{user_id}.json`` from DB."""
    url = f"{_console_base()}/internal/gmail-connections/{int(user_id)}/token-file"
    req = urllib.request.Request(url, headers=_internal_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        log.warning("console token-file refresh failed user_id=%s: %s", user_id, exc)
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    path_str = payload.get("token_path") if isinstance(payload, dict) else None
    if not path_str:
        return None
    path = Path(str(path_str)).expanduser()
    invalidate_gmail_connection_cache()
    return path if path.exists() else None


def list_operator_gmail_clients(*, force_refresh: bool = False) -> list[OperatorMailbox]:
    """Materialise :class:`GmailClient` instances for every connected operator."""
    out: list[OperatorMailbox] = []
    for row in fetch_gmail_connections():
        try:
            uid = int(row.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        email = str(row.get("google_email") or "").strip().lower()
        token_path = row.get("token_path")
        if not token_path and (force_refresh or multi_operator_gmail_enabled()):
            refreshed = refresh_token_file_from_console(uid)
            if refreshed is not None:
                token_path = str(refreshed)
        if not token_path:
            continue
        path = Path(str(token_path)).expanduser()
        if not path.exists():
            continue
        client = GmailClient(credentials_path=path)
        if not client.is_available():
            log.warning("gmail token unavailable for user_id=%s", uid)
            continue
        out.append(OperatorMailbox(user_id=uid, google_email=email, client=client))
    if out:
        return out
    legacy = GmailClient()
    if legacy.is_available():
        return [OperatorMailbox(user_id=0, google_email="legacy", client=legacy)]
    return []
