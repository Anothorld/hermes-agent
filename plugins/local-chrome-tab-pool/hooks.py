"""Hermes plugin hooks — seed per-task page CDP sessions before browser tools run."""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_BROWSER_TOOL_PREFIX = "browser_"
_CLEANUP_WRAPPED = False
_SESSION_INFO_WRAPPED = False
_CREATE_CDP_WRAPPED = False
_SUPERVISOR_WRAPPED = False
_RUN_BROWSER_WRAPPED = False
_POPENV_PATCHED = False
_strip_browser_cdp = threading.local()
_OPEN_FALLBACK_LOCK = threading.Lock()
_TAB_POOL_DIRECT_COMMANDS = frozenset({"snapshot", "eval", "click", "scroll", "back"})

HookResult = Optional[Union[None, Dict[str, str]]]


def _agent_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "f1c85f",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/arnold/agent_prj/.cursor/debug-f1c85f.log",
            "a",
            encoding="utf-8",
        ) as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
    # #endregion


def _cdp_url_is_browser_level(cdp_url: str) -> bool:
    return "/devtools/browser/" in (cdp_url or "").strip().lower()


def _cdp_url_is_page_level(cdp_url: str) -> bool:
    return "/devtools/page/" in (cdp_url or "").strip().lower()


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


def _load_cdp_page_module():
    cached = sys.modules.get("hermes_plugins.local_chrome_tab_pool.internal.cdp_page")
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("internal") / "cdp_page.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.local_chrome_tab_pool.internal.cdp_page",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load cdp_page from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugins.local_chrome_tab_pool.internal.cdp_page"] = module
    spec.loader.exec_module(module)
    return module


cdp_page = _load_cdp_page_module()


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
        _agent_debug_log(
            "H3",
            "hooks:_guard_browser_cdp",
            "blocked foreign target_id",
            {
                "task_id": tid,
                "own_target_id": own_target_id,
                "used_target_id": used_target,
                "method": method,
            },
        )
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


def _evict_legacy_browser_session(task_id: str) -> bool:
    """Drop a stale browser-level session so tab pool can seed a page tab.

    Returns True when a non-tab-pool session was removed.
    """
    from tools import browser_tool

    removed = False
    with browser_tool._cleanup_lock:
        for session_key in _session_keys(task_id):
            existing = browser_tool._active_sessions.get(session_key)
            if existing is None:
                continue
            if existing.get("features", {}).get("tab_pool"):
                continue
            logger.info(
                "Tab pool evicting legacy browser session for task=%s key=%s "
                "(backend=%s)",
                task_id,
                session_key,
                existing.get("cdp_url", "unknown"),
            )
            browser_tool._stop_cdp_supervisor(session_key)
            browser_tool._active_sessions.pop(session_key, None)
            browser_tool._session_last_activity.pop(session_key, None)
            removed = True
            _agent_debug_log(
                "H2",
                "hooks:_evict_legacy_browser_session",
                "evicted legacy browser-level session",
                {"task_id": task_id, "session_key": session_key},
            )
    return removed


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
                _evict_legacy_browser_session(session_key)
            browser_tool._active_sessions[session_key] = _make_session_info(
                session_key, tab_info
            )
            # Update activity inline — ``_update_session_activity`` also takes
            # ``_cleanup_lock`` and would deadlock here (POVISON 701: tab
            # acquired, then run hung forever with no browser_navigate log).
            browser_tool._session_last_activity[session_key] = time.time()
            seeded = True
    return seeded


def _ensure_tab_pool_browser_session(task_id: str) -> Optional[Dict[str, Any]]:
    """Acquire a pooled tab and seed ``browser_tool._active_sessions``.

    Returns the tab-pool session dict when ready, else ``None``.
    """
    if not tab_pool.is_enabled():
        return None

    tid = tab_pool.normalize_task_id(task_id)
    if tid.endswith("::local"):
        return None

    from tools import browser_tool

    browser_tool._start_browser_cleanup_thread()
    browser_tool._update_session_activity(tid)

    with browser_tool._cleanup_lock:
        existing = browser_tool._active_sessions.get(tid)
        if existing is not None and existing.get("features", {}).get("tab_pool"):
            return existing

    _evict_legacy_browser_session(tid)

    try:
        tab_info = tab_pool.acquire(tid)
    except Exception as exc:
        logger.warning(
            "Tab pool could not acquire a tab for task=%s: %s", tid, exc
        )
        return None

    if not _seed_browser_session(tid, tab_info):
        return None

    with browser_tool._cleanup_lock:
        session = dict(browser_tool._active_sessions[tid])

    browser_tool._ensure_cdp_supervisor(tid)
    return session


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
    **_: Any,
) -> HookResult:
    del tool_call_id

    if tool_name == "cleanup_browser":
        return None

    if not _is_browser_tool(tool_name):
        return None

    if not tab_pool.is_enabled():
        return None

    tid = tab_pool.normalize_task_id(task_id)
    if tid.endswith("::local"):
        return None

    try:
        session = _ensure_tab_pool_browser_session(task_id)
        if session is None:
            return {
                "action": "block",
                "message": (
                    f"Local Chrome tab pool could not seed an isolated tab for task={tid} "
                    "(browser session already claimed by another backend). "
                    "Call cleanup_browser for this task or set LOCAL_CHROME_TAB_POOL=0."
                ),
            }
        tab_info = {
            "target_id": (session.get("features") or {}).get("target_id"),
            "cdp_url": session.get("cdp_url"),
        }
        if tool_name == "browser_cdp" and isinstance(args, dict):
            guard_result = _guard_browser_cdp(
                args, str(tab_info.get("target_id") or ""), tid
            )
            if guard_result is not None:
                return guard_result
        _agent_debug_log(
            "H1-H6",
            "hooks:pre_tool_call",
            "tab-pool session before browser tool",
            {
                "tool_name": tool_name,
                "task_id": tid,
                "target_id": tab_info.get("target_id"),
                "tab_pool": bool((session.get("features") or {}).get("tab_pool")),
                "cdp_is_page": _cdp_url_is_page_level(str(tab_info.get("cdp_url") or "")),
                "cdp_is_browser": _cdp_url_is_browser_level(
                    str(tab_info.get("cdp_url") or "")
                ),
                "nav_url": args.get("url") if tool_name == "browser_navigate" else None,
                "wrappers": {
                    "session_info": _SESSION_INFO_WRAPPED,
                    "create_cdp": _CREATE_CDP_WRAPPED,
                    "supervisor": _SUPERVISOR_WRAPPED,
                },
            },
        )
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


def install_session_info_wrapper() -> None:
    """Wrap ``_get_session_info`` so tab pool wins over ``BROWSER_CDP_URL``.

    Gateway runs were creating browser-level CDP sessions from
    ``BROWSER_CDP_URL`` inside ``browser_tool._get_session_info`` before
    (or without) the ``pre_tool_call`` hook seeding a per-task page tab,
    causing concurrent discovery runs to fight over one active tab.
    """
    global _SESSION_INFO_WRAPPED
    if _SESSION_INFO_WRAPPED:
        return

    try:
        from tools import browser_tool
    except ImportError:
        logger.debug("Tab pool: browser_tool unavailable; skip session wrapper")
        return

    if getattr(browser_tool, "_tab_pool_session_info_wrapped", False):
        _SESSION_INFO_WRAPPED = True
        return

    original = browser_tool._get_session_info

    def _get_session_info(
        task_id: Optional[str] = None,
        *,
        session_options: Optional[Any] = None,
    ) -> Dict[str, Any]:
        key = (task_id or "default").strip() or "default"
        pooled = _ensure_tab_pool_browser_session(key)
        if pooled is not None:
            _agent_debug_log(
                "H1",
                "hooks:_get_session_info_wrapper",
                "returning pooled page session",
                {
                    "task_id": key,
                    "target_id": (pooled.get("features") or {}).get("target_id"),
                    "cdp_is_page": _cdp_url_is_page_level(str(pooled.get("cdp_url") or "")),
                },
            )
            return pooled
        if tab_pool.is_enabled() and not tab_pool.normalize_task_id(key).endswith(
            "::local"
        ):
            _agent_debug_log(
                "H1",
                "hooks:_get_session_info_wrapper",
                "tab pool enabled but acquire failed",
                {"task_id": key},
            )
            raise RuntimeError(
                f"Local Chrome tab pool could not acquire an isolated page tab "
                f"for task={key}. Call cleanup_browser for this task or set "
                "LOCAL_CHROME_TAB_POOL=0."
            )
        return original(task_id, session_options=session_options)

    browser_tool._get_session_info = _get_session_info  # type: ignore[method-assign]
    browser_tool._tab_pool_session_info_wrapped = True
    _SESSION_INFO_WRAPPED = True
    logger.info(
        "Tab pool: wrapped browser_tool._get_session_info for per-task page CDP"
    )


def install_create_cdp_session_wrapper() -> None:
    """Block browser-level ``_create_cdp_session`` when tab pool is active.

    ``BROWSER_CDP_URL=http://127.0.0.1:9222`` resolves to a **browser** websocket.
    Unwrapped ``browser_tool._get_session_info`` stores that in
    ``_active_sessions`` even when the tab pool already opened a per-task page
    tab — concurrent runs then share one active tab (agent.log 2026-06-23).
    """
    global _CREATE_CDP_WRAPPED
    if _CREATE_CDP_WRAPPED:
        return

    try:
        from tools import browser_tool
    except ImportError:
        logger.debug("Tab pool: browser_tool unavailable; skip create_cdp wrapper")
        return

    if getattr(browser_tool, "_tab_pool_create_cdp_wrapped", False):
        _CREATE_CDP_WRAPPED = True
        return

    original = browser_tool._create_cdp_session

    def _create_cdp_session(task_id: str, cdp_url: str) -> Dict[str, Any]:
        key = tab_pool.normalize_task_id(task_id or "default")
        if (
            tab_pool.is_enabled()
            and not key.endswith("::local")
            and _cdp_url_is_browser_level(cdp_url)
        ):
            pooled = _ensure_tab_pool_browser_session(key)
            if pooled is not None:
                logger.info(
                    "Tab pool: redirected browser-level CDP session for task=%s "
                    "to isolated page tab target_id=%s",
                    key,
                    (pooled.get("features") or {}).get("target_id"),
                )
                _agent_debug_log(
                    "H2",
                    "hooks:_create_cdp_session_wrapper",
                    "redirected browser-level CDP to page tab",
                    {
                        "task_id": key,
                        "target_id": (pooled.get("features") or {}).get("target_id"),
                    },
                )
                return dict(pooled)
            raise RuntimeError(
                f"Local Chrome tab pool refused a browser-level CDP session for "
                f"task={key}. Call cleanup_browser for this task or set "
                "LOCAL_CHROME_TAB_POOL=0."
            )
        return original(task_id, cdp_url)

    browser_tool._create_cdp_session = _create_cdp_session  # type: ignore[method-assign]
    browser_tool._tab_pool_create_cdp_wrapped = True
    _CREATE_CDP_WRAPPED = True
    logger.info(
        "Tab pool: wrapped browser_tool._create_cdp_session to block browser-level CDP"
    )


def _tab_pool_page_session(task_id: str) -> Optional[Dict[str, Any]]:
    """Return the active tab-pool session dict for ``task_id``, if any."""
    if not tab_pool.is_enabled():
        return None
    tid = tab_pool.normalize_task_id(task_id or "default")
    if tid.endswith("::local"):
        return None
    from tools import browser_tool

    with browser_tool._cleanup_lock:
        info = browser_tool._active_sessions.get(tid)
    if info is None or not info.get("features", {}).get("tab_pool"):
        return None
    cdp_url = str(info.get("cdp_url") or "")
    if not _cdp_url_is_page_level(cdp_url):
        return None
    return dict(info)


def _install_popen_env_strip() -> None:
    """Strip ``BROWSER_CDP_URL`` from agent-browser subprocess env when requested."""
    global _POPENV_PATCHED
    if _POPENV_PATCHED:
        return

    real_popen = subprocess.Popen

    def _popen(*args: Any, **kwargs: Any) -> subprocess.Popen:
        if getattr(_strip_browser_cdp, "active", False):
            env = kwargs.get("env")
            if isinstance(env, dict) and "BROWSER_CDP_URL" in env:
                kwargs = dict(kwargs)
                kwargs["env"] = {
                    key: value for key, value in env.items() if key != "BROWSER_CDP_URL"
                }
        return real_popen(*args, **kwargs)

    subprocess.Popen = _popen  # type: ignore[misc, assignment]
    _POPENV_PATCHED = True


def _try_direct_cdp_open(
    session: Dict[str, Any],
    task_id: str,
    nav_url: str,
    expected_target: str,
    timeout: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Navigate via page CDP websocket; return result or None to fall back."""
    cdp_url = str(session.get("cdp_url") or "")
    if not _cdp_url_is_page_level(cdp_url):
        return None

    effective_timeout = float(timeout or 60)
    result = cdp_page.navigate_open(cdp_url, nav_url, timeout=effective_timeout)
    actual_url = tab_pool.get_target_url(str(expected_target))
    landed_ok = cdp_page.navigation_landed_on_tab(nav_url, actual_url)
    if result.get("success") and not landed_ok:
        data_url = str((result.get("data") or {}).get("url") or "")
        landed_ok = cdp_page.navigation_landed_on_tab(nav_url, data_url)
        if landed_ok:
            actual_url = data_url

    _agent_debug_log(
        "H8",
        "hooks:_try_direct_cdp_open",
        "direct CDP Page.navigate result",
        {
            "task_id": tab_pool.normalize_task_id(task_id),
            "target_id": expected_target,
            "nav_url": nav_url,
            "actual_url": actual_url,
            "direct_success": bool(result.get("success")),
            "url_on_own_tab": landed_ok,
        },
    )
    if result.get("success") and landed_ok:
        return result
    if result.get("success"):
        return {
            "success": False,
            "error": (
                f"Direct CDP navigate reported success but tab {expected_target} "
                f"shows {actual_url!r}, expected {nav_url!r}"
            ),
        }
    return result


def _run_direct_cdp_command(
    session: Dict[str, Any],
    task_id: str,
    command: str,
    args: Optional[List[str]],
    timeout: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Execute a browser command on the pooled page websocket."""
    cdp_url = str(session.get("cdp_url") or "")
    result = cdp_page.run_direct_command(
        cdp_url,
        command,
        args or [],
        timeout=float(timeout or 30),
    )
    if result is None:
        return None
    target_id = (session.get("features") or {}).get("target_id")
    snap_data = result.get("data") or {}
    _agent_debug_log(
        "H10",
        "hooks:_run_direct_cdp_command",
        "direct CDP browser command",
        {
            "task_id": tab_pool.normalize_task_id(task_id),
            "command": command,
            "target_id": target_id,
            "success": bool(result.get("success")),
            "snapshot_len": len(str(snap_data.get("snapshot") or "")),
            "refs_count": len(snap_data.get("refs") or {}),
            "actual_url": tab_pool.get_target_url(str(target_id or "")),
        },
    )
    return result


def install_run_browser_command_wrapper() -> None:
    """Route tab-pool browser commands through direct page CDP (no agent-browser).

    Concurrent agent-browser subprocesses race even with page-level ``--cdp``
    and ``BROWSER_CDP_URL`` stripping. Direct ``Page.navigate`` fixed isolation
    but agent-browser sync for snapshots reintroduced cross-talk risk and still
    returned ``snapshot_len=0`` (2026-06-23 H9 logs). Tab-pool ``open``,
    ``snapshot``, ``eval``, ``click``, ``scroll``, and ``back`` now use
    ``internal/cdp_page.py`` exclusively; agent-browser is fallback only when
    direct CDP navigate fails.
    """
    global _RUN_BROWSER_WRAPPED
    if _RUN_BROWSER_WRAPPED:
        return

    try:
        from tools import browser_tool
    except ImportError:
        logger.debug("Tab pool: browser_tool unavailable; skip run_browser wrapper")
        return

    if getattr(browser_tool, "_tab_pool_run_browser_wrapped", False):
        _RUN_BROWSER_WRAPPED = True
        return

    _install_popen_env_strip()
    original = browser_tool._run_browser_command

    def _run_browser_command(
        task_id: str,
        command: str,
        args: Optional[List[str]] = None,
        timeout: Optional[int] = None,
        _engine_override: Optional[str] = None,
        _retry_after_cdp_recovery: bool = False,
    ) -> Dict[str, Any]:
        session = _tab_pool_page_session(task_id)
        strip_env = session is not None
        expected_target = (session.get("features") or {}).get("target_id") if session else None
        nav_url = (args or [None])[0] if command == "open" and args else None
        direct: Optional[Dict[str, Any]] = None

        if session and command in _TAB_POOL_DIRECT_COMMANDS:
            routed = _run_direct_cdp_command(
                session, task_id, command, args, timeout
            )
            if routed is not None:
                return routed

        if session and command == "open" and nav_url and expected_target:
            direct = _try_direct_cdp_open(
                session,
                task_id,
                str(nav_url),
                str(expected_target),
                timeout,
            )
            if direct is not None and direct.get("success"):
                snap_data = direct.get("data") or {}
                _agent_debug_log(
                    "H10",
                    "hooks:_run_browser_command_wrapper",
                    "direct CDP open with inline snapshot",
                    {
                        "task_id": tab_pool.normalize_task_id(task_id),
                        "target_id": expected_target,
                        "nav_url": nav_url,
                        "snapshot_len": len(str(snap_data.get("snapshot") or "")),
                        "text_len": snap_data.get("text_len"),
                        "refs_count": len(snap_data.get("refs") or {}),
                    },
                )
                return direct

        if strip_env:
            _strip_browser_cdp.active = True
            _agent_debug_log(
                "H7",
                "hooks:_run_browser_command_wrapper",
                "stripping BROWSER_CDP_URL for tab-pool subprocess",
                {
                    "task_id": tab_pool.normalize_task_id(task_id),
                    "command": command,
                    "target_id": expected_target,
                    "nav_url": nav_url,
                },
            )
        try:
            if strip_env and command == "open":
                with _OPEN_FALLBACK_LOCK:
                    result = original(
                        task_id,
                        command,
                        args,
                        timeout,
                        _engine_override=_engine_override,
                        _retry_after_cdp_recovery=_retry_after_cdp_recovery,
                    )
            else:
                result = original(
                    task_id,
                    command,
                    args,
                    timeout,
                    _engine_override=_engine_override,
                    _retry_after_cdp_recovery=_retry_after_cdp_recovery,
                )
        finally:
            _strip_browser_cdp.active = False

        if (
            strip_env
            and command == "open"
            and expected_target
            and nav_url
            and not (direct is not None and direct.get("success"))
        ):
            actual_url = tab_pool.get_target_url(str(expected_target))
            _agent_debug_log(
                "H7",
                "hooks:_run_browser_command_wrapper",
                "post-open target URL check",
                {
                    "task_id": tab_pool.normalize_task_id(task_id),
                    "target_id": expected_target,
                    "nav_url": nav_url,
                    "actual_url": actual_url,
                    "url_on_own_tab": nav_url.split("?", 1)[0] in actual_url
                    or actual_url.split("?", 1)[0] in nav_url,
                },
            )
        return result

    browser_tool._run_browser_command = _run_browser_command  # type: ignore[method-assign]
    browser_tool._tab_pool_run_browser_wrapped = True
    _RUN_BROWSER_WRAPPED = True
    logger.info(
        "Tab pool: wrapped browser_tool._run_browser_command "
        "(direct CDP open/snapshot/eval/click/scroll/back)"
    )


def install_supervisor_wrapper() -> None:
    """Ensure pooled page CDP exists before the CDP supervisor attaches."""
    global _SUPERVISOR_WRAPPED
    if _SUPERVISOR_WRAPPED:
        return

    try:
        from tools import browser_tool
    except ImportError:
        logger.debug("Tab pool: browser_tool unavailable; skip supervisor wrapper")
        return

    if getattr(browser_tool, "_tab_pool_supervisor_wrapped", False):
        _SUPERVISOR_WRAPPED = True
        return

    original = browser_tool._ensure_cdp_supervisor

    def _ensure_cdp_supervisor(task_id: str) -> None:
        key = tab_pool.normalize_task_id(task_id or "default")
        if tab_pool.is_enabled() and not key.endswith("::local"):
            _ensure_tab_pool_browser_session(key)
        original(task_id)

    browser_tool._ensure_cdp_supervisor = _ensure_cdp_supervisor  # type: ignore[method-assign]
    browser_tool._tab_pool_supervisor_wrapped = True
    _SUPERVISOR_WRAPPED = True
    logger.info("Tab pool: wrapped browser_tool._ensure_cdp_supervisor")


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
