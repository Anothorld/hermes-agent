"""Hermes plugin hooks — seed per-task page CDP sessions before browser tools run."""

from __future__ import annotations

import importlib.util
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

_BROWSER_TOOL_PREFIX = "browser_"
_CLEANUP_WRAPPED = False

HookResult = Optional[Union[None, Dict[str, str]]]


def _load_tab_pool_module():
    cached = sys.modules.get("hermes_plugins.local_chrome_tab_pool.internal.tab_pool")
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("internal") / "tab_pool.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.local_chrome_tab_pool.internal.tab_pool",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tab_pool from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugins.local_chrome_tab_pool.internal.tab_pool"] = module
    spec.loader.exec_module(module)
    return module


tab_pool = _load_tab_pool_module()


def _is_browser_tool(tool_name: str) -> bool:
    return tool_name.startswith(_BROWSER_TOOL_PREFIX)


def _session_keys(task_id: str) -> list[str]:
    """Session keys that should receive the pooled tab for this task.

    Only the bare ``task_id`` is seeded. Hybrid ``::local`` sidecar sessions
    (cloud provider + private URL routing) intentionally use headless Chromium
    and are not tab-pooled — see docs/local-chrome-concurrent-tabs.md.
    """
    return [tab_pool.normalize_task_id(task_id)]


# CDP domains that legitimately operate at browser level (no tab scope).
# Everything else is page-scoped and must run inside the task's own pooled tab.
_BROWSER_LEVEL_CDP_DOMAINS = frozenset(
    {"Target", "Browser", "Storage", "SystemInfo", "IO", "Tethering"}
)

# Browser-level Target.* methods that act ON a tab (params.targetId) — these
# must also be restricted to the task's own pooled tab, otherwise a run can
# navigate/close/attach to another run's tab (the POVISON cross-talk bug).
_TARGET_SCOPED_CDP_METHODS = frozenset(
    {
        "Target.attachToTarget",
        "Target.activateTarget",
        "Target.closeTarget",
        "Page.navigate",
    }
)


def _guard_browser_cdp(args: Dict[str, Any], own_target_id: str, tid: str) -> HookResult:
    """Enforce per-task tab ownership for raw ``browser_cdp`` calls.

    The ``browser_cdp`` tool connects to the **browser-level** CDP socket and
    can address any tab by ``target_id`` — bypassing the tab pool entirely.
    Concurrent runs were hijacking each other's tabs this way (confirmed:
    run A ran ``Runtime.evaluate`` inside run B's pooled tab).

    Policy:
      - ``target_id`` (or ``params.targetId``) referring to a foreign tab → block,
        telling the agent its own tab id.
      - Page-scoped method without ``target_id`` → inject the task's own
        pooled tab id so the call lands in the right tab.
      - Browser-level read-only methods (``Target.getTargets`` etc.) pass through.

    Returns:
        None to allow the (possibly arg-rewritten) call, or a block directive.
    """
    method = str(args.get("method") or "")
    domain = method.split(".", 1)[0] if method else ""
    params = args.get("params")
    params_target = (params or {}).get("targetId") if isinstance(params, dict) else None
    used_target = args.get("target_id") or params_target

    if used_target and used_target != own_target_id:
        return {
            "action": "block",
            "message": (
                f"browser_cdp blocked: target_id={used_target} belongs to another "
                f"concurrent run's tab. This task (task={tid}) owns the dedicated "
                f"tab target_id={own_target_id} — pass that target_id instead "
                f"(or omit target_id to have it filled in automatically). Do NOT "
                f"navigate, evaluate in, attach to, or close tabs you do not own; "
                f"use browser_navigate to load pages in your own tab."
            ),
        }

    if not used_target and (
        domain not in _BROWSER_LEVEL_CDP_DOMAINS
        or method in _TARGET_SCOPED_CDP_METHODS
    ):
        # Page-scoped call with no explicit target → pin to the task's own tab.
        args["target_id"] = own_target_id
        if isinstance(params, dict) and "targetId" in params:
            params["targetId"] = own_target_id
    return None


def _make_session_info(session_key: str, tab_info: Dict[str, str]) -> Dict[str, Any]:
    session_name = f"tabpool_{abs(hash(session_key)) & 0xFFFF_FFFF:08x}"
    return {
        "session_name": session_name,
        "bb_session_id": None,
        "cdp_url": tab_info["cdp_url"],
        "features": {
            "cdp_override": True,
            "tab_pool": True,
            "target_id": tab_info["target_id"],
            "tab_pool_owner": tab_pool.normalize_task_id(session_key),
        },
    }


def _seed_browser_session(task_id: str, tab_info: Dict[str, str]) -> bool:
    """Pre-seed browser sessions for ``task_id`` (and hybrid sidecar key).

    Returns:
        True when at least one session key was seeded, False when skipped.
    """
    from tools import browser_tool

    seeded = False
    with browser_tool._cleanup_lock:
        for session_key in _session_keys(task_id):
            existing = browser_tool._active_sessions.get(session_key)
            if existing is not None:
                if existing.get("features", {}).get("tab_pool"):
                    seeded = True
                    continue
                logger.warning(
                    "Tab pool cannot seed task=%s key=%s: session already exists "
                    "without tab_pool (backend=%s). Concurrent runs may cross-talk; "
                    "retry after cleanup_browser or set LOCAL_CHROME_TAB_POOL=0.",
                    task_id,
                    session_key,
                    existing.get("cdp_url", "unknown"),
                )
                continue
            browser_tool._active_sessions[session_key] = _make_session_info(
                session_key, tab_info
            )
            # Update activity inline — ``_update_session_activity`` also takes
            # ``_cleanup_lock`` and would deadlock here (POVISON 701: tab
            # acquired, then run hung forever with no browser_navigate log).
            browser_tool._session_last_activity[session_key] = time.time()
            seeded = True
    return seeded


def _maybe_release(task_id: str) -> None:
    if not task_id:
        return
    if not tab_pool.is_enabled():
        return
    # Sidecar-only cleanup must not close the shared tab while the bare task
    # session may still be active.
    bare = tab_pool.normalize_task_id(task_id)
    if bare != (task_id or "").strip():
        return
    tab_pool.release(bare)


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
) -> HookResult:
    del session_id, tool_call_id

    if tool_name == "cleanup_browser":
        return None

    if not _is_browser_tool(tool_name):
        return None

    if not tab_pool.is_enabled():
        return None

    tid = tab_pool.normalize_task_id(task_id)
    try:
        tab_info = tab_pool.acquire(tid)
        if not _seed_browser_session(tid, tab_info):
            return {
                "action": "block",
                "message": (
                    f"Local Chrome tab pool could not seed an isolated tab for task={tid} "
                    "(browser session already claimed by another backend). "
                    "Call cleanup_browser for this task or set LOCAL_CHROME_TAB_POOL=0."
                ),
            }
        if tool_name == "browser_cdp" and isinstance(args, dict):
            guard_result = _guard_browser_cdp(args, tab_info["target_id"], tid)
            if guard_result is not None:
                return guard_result
    except Exception as exc:
        logger.warning("Tab pool pre_tool_call failed for task=%s: %s", tid, exc)
        return {
            "action": "block",
            "message": (
                f"Local Chrome tab pool could not start/acquire a tab: {exc}. "
                "Run playground/local-chrome-debug/start-debug-chrome.sh manually "
                "or set LOCAL_CHROME_TAB_POOL=0 to disable tab pooling."
            ),
        }
    return None


def install_cleanup_wrapper() -> None:
    """Wrap ``cleanup_browser`` so inactivity reaper also closes pooled tabs."""
    global _CLEANUP_WRAPPED
    if _CLEANUP_WRAPPED:
        return

    if not tab_pool.is_enabled():
        return

    try:
        from tools import browser_tool
    except ImportError:
        logger.debug("Tab pool: browser_tool unavailable; skip cleanup wrapper")
        return

    if getattr(browser_tool, "_tab_pool_cleanup_wrapped", False):
        _CLEANUP_WRAPPED = True
        return

    original = browser_tool.cleanup_browser

    def cleanup_browser(task_id: Optional[str] = None) -> None:
        try:
            original(task_id)
        finally:
            _maybe_release((task_id or "default").strip() or "default")

    browser_tool.cleanup_browser = cleanup_browser  # type: ignore[method-assign]
    browser_tool._tab_pool_cleanup_wrapped = True
    _CLEANUP_WRAPPED = True
    logger.info("Tab pool: wrapped browser_tool.cleanup_browser for tab release")
