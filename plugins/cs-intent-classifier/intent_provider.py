"""Thin HTTP client for cs-ops-bridge seams to call cs-intent-classifier.

Lives in cs-ops-bridge's import namespace (copied/symlinked), NOT in the
classifier package — kept here as the canonical reference implementation.
The seams import this to keep the classifier decoupled: they only need an
HTTP client that respects timeouts and returns the gate_extract dict.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)


def _base_url() -> str:
    return os.environ.get("CS_INTENT_BASE_URL", "http://127.0.0.1:8082").rstrip("/")


def classify(
    *,
    session_id: str,
    env: str,
    subject: str,
    body: str,
    metadata: dict[str, Any],
    timeout: float = 3.0,
) -> Optional[dict[str, Any]]:
    """POST /classify → returns gate_extract dict, or None on failure/transient."""
    url = _base_url() + "/classify"
    payload = json.dumps(
        {"session_id": session_id, "env": env, "subject": subject, "body": body, "metadata": metadata},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        return data.get("gate_extract")
    except urllib.error.HTTPError as exc:
        log.warning("classifier POST /classify HTTP %s: %s", exc.code, _short_err(exc))
        return None
    except Exception as exc:
        log.warning("classifier unreachable: %s", exc)
        return None


def get_gate_extract(*, session_id: str, env: str, timeout: float = 2.0) -> Optional[dict[str, Any]]:
    """GET /gate-extract/{id} → latest gate_extract, or None if not classified."""
    url = _base_url() + f"/gate-extract/{session_id}?env={env}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        log.warning("classifier GET /gate-extract HTTP %s", exc.code)
        return None
    except Exception as exc:
        log.warning("classifier GET /gate-extract failed: %s", exc)
        return None


def health(*, timeout: float = 1.0) -> bool:
    """Probe the classifier service. Used by Console to decide whether to show edit UI."""
    url = _base_url() + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _short_err(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read()[:200].decode("utf-8", errors="replace")
    except Exception:
        return str(exc)
