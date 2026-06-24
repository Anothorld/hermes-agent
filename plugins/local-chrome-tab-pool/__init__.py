"""Local Chrome tab pool plugin.

Gives each agent ``task_id`` its own browser tab on a **single** shared
debug-Chrome profile so concurrent runs do not fight over the active tab.
Chrome is auto-started via ``playground/local-chrome-debug/start-debug-chrome.sh``
when missing.

Disable with ``LOCAL_CHROME_TAB_POOL=0``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_hooks():
    hooks_path = Path(__file__).resolve().with_name("hooks.py")
    module_name = "hermes_plugins.local_chrome_tab_pool.hooks"
    spec = importlib.util.spec_from_file_location(module_name, hooks_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from {hooks_path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.local_chrome_tab_pool"
    spec.loader.exec_module(module)
    return module


def register(ctx) -> None:
    """Register Hermes lifecycle hooks."""
    hooks = _load_hooks()
    hooks.install_session_info_wrapper()
    hooks.install_create_cdp_session_wrapper()
    hooks.install_run_browser_command_wrapper()
    hooks.install_supervisor_wrapper()
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    hooks.install_cleanup_wrapper()
    hooks._agent_debug_log(
        "H1",
        "hooks:register",
        "tab pool plugin registered",
        {
            "session_info_wrapped": hooks._SESSION_INFO_WRAPPED,
            "create_cdp_wrapped": hooks._CREATE_CDP_WRAPPED,
            "run_browser_wrapped": hooks._RUN_BROWSER_WRAPPED,
            "supervisor_wrapped": hooks._SUPERVISOR_WRAPPED,
            "tab_pool_enabled": hooks.tab_pool.is_enabled(),
        },
    )
