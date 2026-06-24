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


def test_pre_tool_call_evicts_legacy_browser_session(hooks_env, monkeypatch):
    """Legacy browser-level CDP sessions must be replaced, not block or reuse."""
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
        "cdp_url": "ws://127.0.0.1:9222/devtools/browser/abc",
        "features": {"cdp_override": True},
    }

    result = hooks.pre_tool_call("browser_snapshot", {}, task_id="run-2")
    assert result is None
    assert browser_tool._active_sessions["run-2"]["features"]["tab_pool"] is True
    assert "/devtools/page/" in browser_tool._active_sessions["run-2"]["cdp_url"]


def test_session_info_wrapper_evicts_legacy_browser_cdp(hooks_env, monkeypatch):
    """Wrapper must not fall back to cached browser-level CDP sessions."""
    hooks = hooks_env
    monkeypatch.setenv("BROWSER_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(
        hooks.tab_pool,
        "acquire",
        lambda task_id: {
            "target_id": "PAGE-legacy",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/PAGE-legacy",
        },
    )

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()
    browser_tool._active_sessions["kol-campaign:LIVE:LEGACY"] = {
        "cdp_url": "ws://127.0.0.1:9222/devtools/browser/stale",
        "features": {"cdp_override": True},
    }
    browser_tool._tab_pool_session_info_wrapped = False
    hooks._SESSION_INFO_WRAPPED = False
    hooks.install_session_info_wrapper()

    info = browser_tool._get_session_info("kol-campaign:LIVE:LEGACY")
    assert info["features"]["tab_pool"] is True
    assert "/devtools/page/" in info["cdp_url"]


def test_session_info_wrapper_prefers_tab_pool_over_browser_cdp(
    hooks_env, monkeypatch,
):
    """Regression: BROWSER_CDP_URL must not bypass per-task page tabs."""
    hooks = hooks_env
    monkeypatch.setenv(
        "BROWSER_CDP_URL",
        "http://127.0.0.1:9222",
    )
    monkeypatch.setattr(
        hooks.tab_pool,
        "acquire",
        lambda task_id: {
            "target_id": f"PAGE-{task_id}",
            "cdp_url": f"ws://127.0.0.1:9222/devtools/page/PAGE-{task_id}",
        },
    )

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()
    browser_tool._tab_pool_session_info_wrapped = False
    hooks._SESSION_INFO_WRAPPED = False
    hooks.install_session_info_wrapper()

    info_a = browser_tool._get_session_info("kol-campaign:LIVE:CAMP-A")
    info_b = browser_tool._get_session_info("kol-campaign:LIVE:CAMP-B")

    assert info_a["features"]["tab_pool"] is True
    assert info_b["features"]["tab_pool"] is True
    assert "/devtools/page/" in info_a["cdp_url"]
    assert "/devtools/page/" in info_b["cdp_url"]
    assert info_a["cdp_url"] != info_b["cdp_url"]


def test_create_cdp_session_wrapper_blocks_browser_level(hooks_env, monkeypatch):
    """Regression: _create_cdp_session must not persist browser-level CDP when pool is on."""
    hooks = hooks_env
    monkeypatch.setattr(
        hooks.tab_pool,
        "acquire",
        lambda task_id: {
            "target_id": "PAGE-block",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/PAGE-block",
        },
    )

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()
    browser_tool._tab_pool_create_cdp_wrapped = False
    hooks._CREATE_CDP_WRAPPED = False
    hooks.install_create_cdp_session_wrapper()

    info = browser_tool._create_cdp_session(
        "kol-campaign:LIVE:CAMP-X",
        "ws://127.0.0.1:9222/devtools/browser/shared",
    )
    assert info["features"]["tab_pool"] is True
    assert "/devtools/page/" in info["cdp_url"]


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


def test_run_browser_command_strips_browser_cdp_url_from_subprocess_env(
    hooks_env, monkeypatch
):
    """Regression: BROWSER_CDP_URL in process env must not override page --cdp."""
    import os
    import subprocess

    hooks = hooks_env
    captured_envs: list[dict] = []

    def capturing_popen(*args, **kwargs):
        env = kwargs.get("env")
        if isinstance(env, dict):
            captured_envs.append(dict(env))
        proc = mock.Mock()
        proc.wait.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr(subprocess, "Popen", capturing_popen)

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._session_last_activity.clear()
    browser_tool._active_sessions["run-env"] = hooks._make_session_info(
        "run-env",
        {
            "target_id": "T-page",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/T-page",
        },
    )
    browser_tool._tab_pool_run_browser_wrapped = False
    hooks._RUN_BROWSER_WRAPPED = False
    hooks._POPENV_PATCHED = False

    def original_run(*args, **kwargs):
        subprocess.Popen(
            ["agent-browser"],
            env={**os.environ, "BROWSER_CDP_URL": "http://127.0.0.1:9222"},
        )
        return {"success": True, "data": {}}

    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        original_run,
        raising=False,
    )
    monkeypatch.setattr(
        hooks.tab_pool,
        "get_target_url",
        lambda target_id: "https://example.com/",
    )
    hooks.install_run_browser_command_wrapper()

    browser_tool._run_browser_command("run-env", "open", ["https://example.com/"])

    assert captured_envs, "expected agent-browser subprocess with env"
    assert "BROWSER_CDP_URL" not in captured_envs[0]


def test_run_browser_command_uses_direct_cdp_open_for_tab_pool(hooks_env, monkeypatch):
    """Tab-pool ``open`` should use direct CDP only — no agent-browser sync."""
    hooks = hooks_env
    calls = {"agent_browser": 0, "direct": 0}

    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._active_sessions["run-direct"] = hooks._make_session_info(
        "run-direct",
        {
            "target_id": "T-direct",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/T-direct",
        },
    )
    browser_tool._tab_pool_run_browser_wrapped = False
    hooks._RUN_BROWSER_WRAPPED = False
    hooks._POPENV_PATCHED = False

    def fake_navigate_open(cdp_url, url, *, timeout=60.0):
        calls["direct"] += 1
        return {
            "success": True,
            "data": {
                "url": url,
                "title": "ok",
                "snapshot": "[@e1] a https://example.com/ \"Example\"",
                "refs": {"@e1": {}},
                "text_len": 120,
            },
        }

    monkeypatch.setattr(hooks.cdp_page, "navigate_open", fake_navigate_open)
    monkeypatch.setattr(
        hooks.tab_pool,
        "get_target_url",
        lambda target_id: "https://www.instagram.com/example/",
    )
    monkeypatch.setattr(
        hooks.cdp_page,
        "navigation_landed_on_tab",
        lambda expected, actual: True,
    )

    def original_run(*args, **kwargs):
        calls["agent_browser"] += 1
        return {"success": True, "data": {}}

    monkeypatch.setattr(browser_tool, "_run_browser_command", original_run, raising=False)
    hooks.install_run_browser_command_wrapper()

    result = browser_tool._run_browser_command(
        "run-direct",
        "open",
        ["https://www.instagram.com/example/"],
    )

    assert result["success"] is True
    assert calls["direct"] == 1
    assert calls["agent_browser"] == 0
    assert result["data"]["snapshot"]


def test_run_browser_command_routes_snapshot_via_direct_cdp(hooks_env, monkeypatch):
    hooks = hooks_env
    from tools import browser_tool

    browser_tool._active_sessions.clear()
    browser_tool._active_sessions["run-snap"] = hooks._make_session_info(
        "run-snap",
        {
            "target_id": "T-snap",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/T-snap",
        },
    )
    browser_tool._tab_pool_run_browser_wrapped = False
    hooks._RUN_BROWSER_WRAPPED = False
    hooks._POPENV_PATCHED = False

    monkeypatch.setattr(
        hooks.cdp_page,
        "run_direct_command",
        lambda cdp_url, command, args, timeout=30.0: {
            "success": True,
            "data": {"snapshot": "body text", "refs": {}},
        },
    )

    def original_run(*args, **kwargs):
        raise AssertionError("agent-browser must not run for tab-pool snapshot")

    monkeypatch.setattr(browser_tool, "_run_browser_command", original_run, raising=False)
    hooks.install_run_browser_command_wrapper()

    result = browser_tool._run_browser_command("run-snap", "snapshot", ["-c"])
    assert result["success"] is True
    assert result["data"]["snapshot"] == "body text"
