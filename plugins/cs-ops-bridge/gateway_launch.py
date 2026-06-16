"""Gateway launch helpers — retry, dedup, optional SSE drain."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
_inflight: set[str] = set()


def launch_dedup_key(session_id: str, message_id: str) -> str:
    return f"{session_id}:{message_id}"


def try_acquire_launch(key: str) -> bool:
    with _lock:
        if key in _inflight:
            return False
        _inflight.add(key)
        return True


def release_launch(key: str) -> None:
    with _lock:
        _inflight.discard(key)


def post_run_with_retry(
    *,
    base: str,
    api_key: Optional[str],
    body: dict[str, Any],
    max_attempts: int = 4,
) -> Optional[dict[str, Any]]:
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = json.dumps(body).encode("utf-8")
    url = f"{base.rstrip('/')}/v1/runs"
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503, 504) and attempt < max_attempts:
                log.warning("gateway POST /v1/runs HTTP %s — retry %s/%s", exc.code, attempt, max_attempts)
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue
            log.error("gateway POST /v1/runs failed HTTP %s", exc.code)
            return None
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                log.warning("gateway unreachable — retry %s/%s: %s", attempt, max_attempts, exc)
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue
            log.error("gateway POST /v1/runs failed: %s", exc)
            return None
    return None


def drain_run_events(*, base: str, api_key: Optional[str], run_id: str, timeout_sec: float = 5.0) -> None:
    """Best-effort SSE drain so gateway concurrency slots free promptly."""
    if os.environ.get("CS_OPS_GATEWAY_DRAIN", "true").lower() in ("0", "false", "no"):
        return
    url = f"{base.rstrip('/')}/v1/runs/{run_id}/events"
    headers = {"Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            resp.read(4096)
    except Exception as exc:
        log.debug("gateway drain skipped for %s: %s", run_id, exc)
