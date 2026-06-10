"""Hermes plugin hooks — seed per-task page CDP sessions before browser tools run."""

from __future__ import annotations

import importlib.util
import logging
import os
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
    del args, session_id, tool_call_id

    if tool_name == "cleanup_browser":
        return None

    if not _is_browser_tool(tool_name):
        return None

    if not tab_pool.is_enabled():
        # #region agent log
        try:
            import json as _json

            with open("/Users/arnold/agent_prj/.cursor/debug-46ec18.log", "a") as _df:
                _df.write(
                    _json.dumps(
                        {
                            "sessionId": "46ec18",
                            "hypothesisId": "H-pool-disabled",
                            "location": "hooks.py:pre_tool_call",
                            "message": "tab pool skipped",
                            "data": {
                                "task_id": task_id,
                                "tool": tool_name,
                                "LOCAL_CHROME_TAB_POOL": os.environ.get(
                                    "LOCAL_CHROME_TAB_POOL"
                                ),
                                "LOCAL_CHROME_FORCE_SHARED_CDP": os.environ.get(
                                    "LOCAL_CHROME_FORCE_SHARED_CDP"
                                ),
                            },
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
        # #endregion
        return None

    tid = tab_pool.normalize_task_id(task_id)
    try:
        tab_info = tab_pool.acquire(tid)
        # #region agent log
        try:
            import json as _json

            with open("/Users/arnold/agent_prj/.cursor/debug-46ec18.log", "a") as _df:
                _df.write(
                    _json.dumps(
                        {
                            "sessionId": "46ec18",
                            "hypothesisId": "H-tab-seed",
                            "location": "hooks.py:pre_tool_call",
                            "message": "tab pool seeding browser session",
                            "data": {
                                "task_id": tid,
                                "tool": tool_name,
                                "target_id": tab_info.get("target_id"),
                                "cdp_is_page_level": "/devtools/page/"
                                in str(tab_info.get("cdp_url", "")),
                            },
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
        # #endregion
        if not _seed_browser_session(tid, tab_info):
            return {
                "action": "block",
                "message": (
                    f"Local Chrome tab pool could not seed an isolated tab for task={tid} "
                    "(browser session already claimed by another backend). "
                    "Call cleanup_browser for this task or set LOCAL_CHROME_TAB_POOL=0."
                ),
            }
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
