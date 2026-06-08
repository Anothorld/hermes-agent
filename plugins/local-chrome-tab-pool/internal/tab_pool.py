#!/usr/bin/env python3
"""Per-task CDP tab pool for concurrent local-Chrome agent runs.

Chrome cannot attach two processes to the same ``--user-data-dir`` at once.
The supported pattern is **one debug Chrome + many tabs**, each agent task
getting its own page-level ``webSocketDebuggerUrl`` so navigation/clicks
do not cross-talk while cookies/login state stay shared.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 9222
_AUTOLAUNCH_TIMEOUT_S = 25.0

_lock = threading.Lock()
_autolaunch_lock = threading.Lock()
_task_tabs: Dict[str, Dict[str, str]] = {}


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _external_browser_cdp_configured() -> bool:
    """True when the operator wired an external shared CDP endpoint.

    ``start-debug-chrome.sh`` sets ``BROWSER_CDP_URL`` for the proven
    shared-connection mode — either the stable HTTP discovery form
    (``http://127.0.0.1:9222``) or a browser-level ws (``…/devtools/browser/…``).
    That shared connection and the per-task page-level tab pool must not both be
    active at once — running both produced the POVISON 686/690 hang (tab
    acquired, then the agent-browser daemon stalled). When such an endpoint is
    present we step aside so the shared connection drives the browser directly.

    A page-level ws (``…/devtools/page/…``) is a single target, not a shared
    endpoint, so it does NOT count.
    """
    cdp = os.environ.get("BROWSER_CDP_URL", "").strip().lower()
    if not cdp or "/devtools/page/" in cdp:
        return False
    return cdp.startswith(("http://", "https://", "ws://", "wss://"))


def is_enabled() -> bool:
    """Return True when per-task tab pooling should be active."""
    if not _truthy_env("LOCAL_CHROME_TAB_POOL", default=True):
        return False
    if _external_browser_cdp_configured():
        return False
    return True


def normalize_task_id(task_id: Optional[str]) -> str:
    """Return the bare session key used for tab ownership tracking."""
    key = (task_id or "default").strip() or "default"
    if key.endswith("::local"):
        return key[: -len("::local")]
    return key


def _debug_port() -> int:
    raw = os.environ.get("DEBUG_CHROME_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_PORT


def _base_http_url() -> str:
    return f"http://127.0.0.1:{_debug_port()}"


def _launcher_script() -> Optional[Path]:
    override = os.environ.get("HERMES_LOCAL_CHROME_LAUNCHER", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None

    # Bundled plugin layout: .../plugins/local-chrome-tab-pool/internal/tab_pool.py
    plugin_root = Path(__file__).resolve().parents[1]
    repo_playground = plugin_root.parents[1] / "playground" / "local-chrome-debug"
    candidates = (
        repo_playground / "start-debug-chrome.sh",
        plugin_root / "scripts" / "start-debug-chrome.sh",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _http_json(
    url: str,
    *,
    method: str = "GET",
    timeout: float = 10.0,
) -> Any:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def probe_chrome() -> bool:
    """Return True when debug Chrome exposes ``/json/version``."""
    try:
        _http_json(f"{_base_http_url()}/json/version", timeout=2.0)
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False


def _close_target_id(target_id: str) -> None:
    """Best-effort close of a CDP target by id (orphan cleanup)."""
    if not target_id:
        return
    try:
        _http_json(
            f"{_base_http_url()}/json/close/{target_id}",
            timeout=5.0,
        )
        logger.debug("Tab pool closed orphan target %s", target_id)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("Tab pool orphan close for %s failed: %s", target_id, exc)


def ensure_chrome_running() -> None:
    """Start (or reuse) the shared debug Chrome via ``start-debug-chrome.sh``."""
    if probe_chrome():
        return

    with _autolaunch_lock:
        if probe_chrome():
            return

        script = _launcher_script()
        if script is None:
            raise RuntimeError(
                "Local Chrome tab pool: debug Chrome is not running and "
                "start-debug-chrome.sh was not found. Set HERMES_LOCAL_CHROME_LAUNCHER "
                "or install playground/local-chrome-debug/start-debug-chrome.sh."
            )

        logger.info("Tab pool auto-starting debug Chrome via %s", script)
        try:
            # DEBUG_CHROME_SKIP_ENV: launch Chrome but do NOT write
            # BROWSER_CDP_URL. The pool drives per-run page-level tabs directly;
            # writing a shared browser endpoint would flip the next restart into
            # shared-connection mode and disable the pool.
            result = subprocess.run(
                ["bash", str(script), "start"],
                timeout=_AUTOLAUNCH_TIMEOUT_S,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "DEBUG_CHROME_SKIP_ENV": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Local Chrome autostart timed out after {_AUTOLAUNCH_TIMEOUT_S:.0f}s"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip().splitlines()
            tail = stderr[-1] if stderr else "(no stderr)"
            raise RuntimeError(
                f"Local Chrome autostart failed (exit {result.returncode}): {tail}"
            )

        if not probe_chrome():
            raise RuntimeError(
                f"Local Chrome autostart finished but port {_debug_port()} is not ready"
            )


def _create_tab() -> Dict[str, str]:
    """Open a new about:blank tab and return its CDP metadata."""
    ensure_chrome_running()
    target = _http_json(
        f"{_base_http_url()}/json/new?about:blank",
        method="PUT",
        timeout=15.0,
    )
    cdp_url = str(target.get("webSocketDebuggerUrl") or "").strip()
    target_id = str(target.get("id") or "").strip()
    if not cdp_url or not target_id:
        raise RuntimeError(
            f"Chrome /json/new did not return webSocketDebuggerUrl/id: {target!r}"
        )
    return {"cdp_url": cdp_url, "target_id": target_id}


def reap_orphan_blank_tabs() -> int:
    """Close untracked ``about:blank`` page targets left behind by dead runs.

    A force-stopped or crashed agent run never reaches ``cleanup_browser``, so
    its pooled ``about:blank`` tab leaks. These accumulate in the shared debug
    Chrome and slow every subsequent CDP attach (POVISON 686: an empty tab was
    opened but navigation never started). Reaping is deliberately conservative —
    only page targets that are **both** untracked by this pool **and** still on
    ``about:blank`` are closed, so real navigated pages and live pooled tabs are
    never touched.

    Returns:
        Count of orphan blank tabs closed (0 when Chrome is down/unreachable).
    """
    if not probe_chrome():
        return 0
    try:
        targets = _http_json(f"{_base_http_url()}/json/list", timeout=5.0)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("Tab pool orphan reap skipped (list failed): %s", exc)
        return 0
    if not isinstance(targets, list):
        return 0

    with _lock:
        tracked = {info.get("target_id") for info in _task_tabs.values()}

    closed = 0
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        target_id = str(target.get("id") or "")
        url = str(target.get("url") or "").strip()
        if not target_id or target_id in tracked:
            continue
        if url not in ("", "about:blank"):
            continue
        _close_target_id(target_id)
        closed += 1
    if closed:
        logger.info("Tab pool reaped %d orphan about:blank tab(s)", closed)
    return closed


def acquire(task_id: str) -> Dict[str, str]:
    """Create (or return cached) isolated tab for ``task_id``.

    Returns:
        Dict with ``cdp_url`` (page-level websocket) and ``target_id``.
    """
    key = normalize_task_id(task_id)

    with _lock:
        cached = _task_tabs.get(key)
        if cached:
            return dict(cached)

    # Best-effort hygiene: clear leaked blank tabs from prior killed runs
    # before opening a new one, so the shared Chrome does not accumulate dead
    # about:blank targets that slow CDP attaches.
    try:
        reap_orphan_blank_tabs()
    except Exception as exc:  # noqa: BLE001 — hygiene must never block acquire
        logger.debug("Tab pool orphan reap errored (non-fatal): %s", exc)

    info = _create_tab()

    with _lock:
        existing = _task_tabs.get(key)
        if existing:
            _close_target_id(info["target_id"])
            return dict(existing)
        _task_tabs[key] = dict(info)
        logger.info("Tab pool acquired tab %s for task=%s", info["target_id"], key)
        return dict(info)


def release(task_id: str) -> bool:
    """Close the tab owned by ``task_id``. Returns True when a tab was closed."""
    key = normalize_task_id(task_id)
    with _lock:
        info = _task_tabs.pop(key, None)
    if not info:
        return False

    target_id = info.get("target_id", "")
    if not target_id:
        return False

    try:
        _http_json(
            f"{_base_http_url()}/json/close/{target_id}",
            timeout=5.0,
        )
        logger.info("Tab pool released tab %s for task=%s", target_id, key)
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Tab pool failed to close tab %s for task=%s: %s", target_id, key, exc
        )
        return False


def release_all() -> int:
    """Close every tracked tab. Returns count closed."""
    with _lock:
        keys = list(_task_tabs.keys())
    closed = 0
    for key in keys:
        if release(key):
            closed += 1
    return closed


def list_orphan_tabs() -> list[Dict[str, Any]]:
    """Return Chrome page targets not currently tracked by the pool (debug helper)."""
    if not probe_chrome():
        return []
    try:
        targets = _http_json(f"{_base_http_url()}/json/list", timeout=5.0)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(targets, list):
        return []

    with _lock:
        tracked = {info["target_id"] for info in _task_tabs.values()}

    orphans = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("type") != "page":
            continue
        target_id = str(target.get("id") or "")
        if target_id and target_id not in tracked:
            orphans.append(target)
    return orphans
