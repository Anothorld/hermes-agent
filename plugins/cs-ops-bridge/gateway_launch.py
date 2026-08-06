"""Gateway launch helpers — retry, dedup, optional SSE drain."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
_inflight: set[str] = set()


@dataclass(frozen=True)
class PostRunResult:
    """Result of POST /v1/runs.

    ``ok`` is True on success (``data`` holds the gateway response). On failure,
    ``transient`` distinguishes retryable gateway states (429/5xx/unreachable)
    from permanent errors — callers re-queue transient failures to ``pending``
    instead of marking the session ``failed``.
    """
    ok: bool
    data: Optional[dict[str, Any]] = None
    transient: bool = False


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
) -> PostRunResult:
    """POST /v1/runs with exponential backoff.

    Returns ``PostRunResult(ok=True, data=...)`` on success. On failure returns
    ``PostRunResult(ok=False, transient=True)`` when the gateway was busy
    (429/502/503/504) or unreachable — these are retried up to ``max_attempts``
    and the caller should re-queue the session to ``pending`` rather than fail
    it. Other HTTP errors are non-transient (``transient=False``).
    """
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = json.dumps(body).encode("utf-8")
    url = f"{base.rstrip('/')}/v1/runs"
    delay = 1.0
    last_transient = False
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            data = json.loads(raw.decode("utf-8")) if raw else {}
            return PostRunResult(ok=True, data=data)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503, 504) and attempt < max_attempts:
                log.warning("gateway POST /v1/runs HTTP %s — retry %s/%s", exc.code, attempt, max_attempts)
                last_transient = True
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue
            # Exhausted retries on a transient code, OR a non-transient code (4xx other).
            transient = exc.code in (429, 502, 503, 504)
            log.error("gateway POST /v1/runs failed HTTP %s (transient=%s)", exc.code, transient)
            return PostRunResult(ok=False, transient=transient)
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                log.warning("gateway unreachable — retry %s/%s: %s", attempt, max_attempts, exc)
                last_transient = True
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue
            log.error("gateway POST /v1/runs failed: %s", exc)
            # Unreachable gateway is transient (it may come back).
            return PostRunResult(ok=False, transient=True)
    return PostRunResult(ok=False, transient=last_transient)


def stop_run(*, base: str, api_key: Optional[str], run_id: str) -> bool:
    """Best-effort POST /v1/runs/{id}/stop — free gateway slot when operator superseded resume."""
    rid = (run_id or "").strip()
    if not rid:
        return False
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base.rstrip('/')}/v1/runs/{rid}/stop"
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 409):
            log.info("gateway stop run=%s HTTP %s (already finished)", rid, exc.code)
            return True
        log.warning("gateway stop run failed run=%s HTTP %s", rid, exc.code)
        return False
    except urllib.error.URLError as exc:
        log.warning("gateway stop run failed run=%s: %s", rid, exc)
        return False


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
        log.info("cs.launch.drain_skipped run=%s reason=%s", run_id, exc)


def get_run_status(*, base: str, api_key: Optional[str], run_id: str) -> Optional[dict]:
    """Query gateway GET /v1/runs/{run_id} for pollable run status.

    Returns the run status dict (contains ``status`` field like
    ``completed``/``failed``/``running``/``queued``) or ``None`` on 404
    (run cleaned up by gateway = assumed ended) or query failure.
    """
    rid = (run_id or "").strip()
    if not rid:
        return None
    url = f"{base.rstrip('/')}/v1/runs/{rid}"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        log.warning("get_run_status failed run=%s HTTP %s", rid, exc.code)
        return None
    except Exception as exc:
        log.warning("get_run_status failed run=%s: %s", rid, exc)
        return None
