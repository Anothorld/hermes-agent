"""Tests for local-chrome-tab-pool plugin hooks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


def _load_hooks_module():
    plugin_dir = Path(__file__).resolve().parents[1]
    hooks_path = plugin_dir / "hooks.py"
    spec = importlib.util.spec_from_file_location(
        "local_chrome_tab_pool_hooks_test",
        hooks_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hooks_env(monkeypatch):
    hooks = _load_hooks_module()
    hooks.tab_pool._task_tabs.clear()
    monkeypatch.setenv("LOCAL_CHROME_TAB_POOL", "1")
    # The pool steps aside when a browser-level shared CDP endpoint is set;
    # isolate so these tests exercise the active-pool path regardless of the
    # ambient environment.
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    return hooks


def test_pre_tool_call_seeds_browser_session(hooks_env, monkeypatch):
    hooks = hooks_env
    monkeypatch.setattr(
        hooks.tab_pool,
        "acquire",
        lambda task_id: {
            "target_id": "T42",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/T42",
        },
    )

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()

    result = hooks.pre_tool_call("browser_navigate", {}, task_id="run-1")
    assert result is None
    assert "run-1" in browser_tool._active_sessions
    assert browser_tool._active_sessions["run-1"]["features"]["tab_pool"] is True
    assert "run-1" in browser_tool._session_last_activity


def test_seed_session_does_not_deadlock_on_real_cleanup_lock(hooks_env, monkeypatch):
    """Regression: seeding must not call _update_session_activity under _cleanup_lock."""
    hooks = hooks_env
    monkeypatch.setattr(
        hooks.tab_pool,
        "acquire",
        lambda task_id: {
            "target_id": "T42",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/T42",
        },
    )

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()

    result = hooks.pre_tool_call("browser_navigate", {}, task_id="run-deadlock")
    assert result is None
    assert "run-deadlock" in browser_tool._active_sessions
    assert "run-deadlock" in browser_tool._session_last_activity


def test_pre_tool_call_blocks_when_session_already_claimed(hooks_env, monkeypatch):
    hooks = hooks_env
    monkeypatch.setattr(
        hooks.tab_pool,
        "acquire",
        lambda task_id: {
            "target_id": "T99",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/T99",
        },
    )

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()
    browser_tool._active_sessions["run-2"] = {
        "cdp_url": "ws://browser-level",
        "features": {},
    }

    result = hooks.pre_tool_call("browser_snapshot", {}, task_id="run-2")
    assert result is not None
    assert result["action"] == "block"


def test_cleanup_wrapper_releases_only_bare_task(hooks_env, monkeypatch):
    hooks = hooks_env
    monkeypatch.setattr(hooks.tab_pool, "is_enabled", lambda: True)
    released = []
    monkeypatch.setattr(
        hooks.tab_pool, "release", lambda task_id: released.append(task_id) or True
    )

    from tools import browser_tool

    calls = []

    def original_cleanup(task_id=None):
        calls.append(task_id)

    monkeypatch.setattr(browser_tool, "cleanup_browser", original_cleanup, raising=False)
    browser_tool._tab_pool_cleanup_wrapped = False
    hooks._CLEANUP_WRAPPED = False
    hooks.install_cleanup_wrapper()

    browser_tool.cleanup_browser("task-z::local")
    assert calls == ["task-z::local"]
    assert released == []

    browser_tool.cleanup_browser("task-z")
    assert calls == ["task-z::local", "task-z"]
    assert released == ["task-z"]
