"""Tests for local-chrome tab pool (canonical internal module)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

import pytest


def _load_tab_pool_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "internal"
        / "tab_pool.py"
    )
    spec = importlib.util.spec_from_file_location("tab_pool_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tab_pool(monkeypatch):
    monkeypatch.delenv("LOCAL_CHROME_TAB_POOL", raising=False)
    module = _load_tab_pool_module()
    module._task_tabs.clear()
    return module


def test_is_enabled_default_on(tab_pool, monkeypatch):
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    assert tab_pool.is_enabled() is True


def test_is_enabled_can_disable(tab_pool, monkeypatch):
    monkeypatch.setenv("LOCAL_CHROME_TAB_POOL", "0")
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    assert tab_pool.is_enabled() is False


def test_is_enabled_defers_to_shared_cdp(tab_pool, monkeypatch):
    monkeypatch.delenv("LOCAL_CHROME_TAB_POOL", raising=False)
    # Browser-level ws shared endpoint → pool steps aside.
    monkeypatch.setenv(
        "BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/abc-123",
    )
    assert tab_pool.is_enabled() is False
    # Stable HTTP discovery endpoint → also a shared endpoint → step aside.
    monkeypatch.setenv("BROWSER_CDP_URL", "http://127.0.0.1:9222")
    assert tab_pool.is_enabled() is False
    # A page-level ws is a single target, not a shared endpoint → pool stays on.
    monkeypatch.setenv(
        "BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/page/PAGE1",
    )
    assert tab_pool.is_enabled() is True
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    assert tab_pool.is_enabled() is True


def test_normalize_task_id_strips_sidecar_suffix(tab_pool):
    assert tab_pool.normalize_task_id("abc::local") == "abc"
    assert tab_pool.normalize_task_id("abc") == "abc"
    assert tab_pool.normalize_task_id(None) == "default"


def test_acquire_creates_tab(tab_pool, monkeypatch):
    calls = []

    def fake_http_json(url, *, method="GET", timeout=10.0):
        calls.append((url, method))
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/ABC"}
        if "/json/new" in url:
            return {
                "id": "TAB1",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/TAB1",
            }
        if "/json/close/" in url:
            return {"success": True}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(tab_pool, "_http_json", fake_http_json)
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "cdp_ws_healthy", lambda timeout=3.0: True)

    info = tab_pool.acquire("task-abc")
    assert info["target_id"] == "TAB1"
    assert info["cdp_url"].endswith("/devtools/page/TAB1")

    again = tab_pool.acquire("task-abc")
    assert again == info
    assert sum(1 for url, method in calls if method == "PUT") == 1


def test_acquire_closes_orphan_when_race_lost(tab_pool, monkeypatch):
    closed = []
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: closed.append(tid))

    def fake_create_and_race():
        info = {"target_id": "ORPHAN", "cdp_url": "ws://127.0.0.1/devtools/page/ORPHAN"}
        tab_pool._task_tabs["task-race"] = {
            "target_id": "WINNER",
            "cdp_url": "ws://127.0.0.1/devtools/page/WINNER",
        }
        return info

    monkeypatch.setattr(tab_pool, "_create_tab", fake_create_and_race)
    tab_pool._task_tabs.clear()

    info = tab_pool.acquire("task-race")
    assert info["target_id"] == "WINNER"
    assert closed == ["ORPHAN"]


def test_release_closes_tab(tab_pool, monkeypatch):
    closed = []

    def fake_http_json(url, *, method="GET", timeout=10.0):
        if "/json/new" in url:
            return {
                "id": "TAB9",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/TAB9",
            }
        if "/json/close/TAB9" in url:
            closed.append(url)
            return {"success": True}
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://x"}
        raise AssertionError(url)

    monkeypatch.setattr(tab_pool, "_http_json", fake_http_json)
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "cdp_ws_healthy", lambda timeout=3.0: True)

    tab_pool.acquire("task-x")
    assert tab_pool.release("task-x") is True
    assert closed
    assert tab_pool.release("task-x") is False


def test_release_normalizes_sidecar_task_id(tab_pool, monkeypatch):
    tab_pool._task_tabs["task-y"] = {
        "target_id": "T1",
        "cdp_url": "ws://127.0.0.1:9222/devtools/page/T1",
    }
    closed = []

    def fake_http_json(url, *, method="GET", timeout=10.0):
        if "/json/close/T1" in url:
            closed.append(url)
            return {"success": True}
        raise AssertionError(url)

    monkeypatch.setattr(tab_pool, "_http_json", fake_http_json)
    assert tab_pool.release("task-y::local") is True
    assert closed


def test_reap_orphan_blank_tabs_closes_only_untracked_blanks(tab_pool, monkeypatch):
    # One tracked pool tab must be preserved even though it's about:blank.
    tab_pool._task_tabs["live"] = {
        "target_id": "TRACKED",
        "cdp_url": "ws://127.0.0.1:9222/devtools/page/TRACKED",
    }
    listing = [
        {"type": "page", "id": "TRACKED", "url": "about:blank"},   # tracked → keep
        {"type": "page", "id": "ORPHAN1", "url": "about:blank"},   # leaked → close
        {"type": "page", "id": "ORPHAN2", "url": ""},              # leaked → close
        {"type": "page", "id": "REALPAGE", "url": "https://ig.com/x"},  # real → keep
        {"type": "background_page", "id": "BG", "url": "about:blank"},  # not page → keep
    ]
    closed = []
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: closed.append(tid))
    monkeypatch.setattr(
        tab_pool, "_http_json",
        lambda url, *, method="GET", timeout=10.0: listing,
    )

    assert tab_pool.reap_orphan_blank_tabs() == 2
    assert set(closed) == {"ORPHAN1", "ORPHAN2"}


def test_reap_orphan_blank_tabs_noop_when_chrome_down(tab_pool, monkeypatch):
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: False)
    monkeypatch.setattr(
        tab_pool, "_http_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    assert tab_pool.reap_orphan_blank_tabs() == 0


def test_acquire_reaps_orphans_before_creating(tab_pool, monkeypatch):
    reaped = {"n": 0}

    def fake_reap():
        reaped["n"] += 1
        return 1

    monkeypatch.setattr(tab_pool, "reap_orphan_blank_tabs", fake_reap)
    monkeypatch.setattr(
        tab_pool, "_create_tab",
        lambda: {"target_id": "T", "cdp_url": "ws://127.0.0.1/devtools/page/T"},
    )
    tab_pool._task_tabs.clear()
    tab_pool.acquire("fresh-task")
    assert reaped["n"] == 1
    # Cached path must NOT reap again.
    tab_pool.acquire("fresh-task")
    assert reaped["n"] == 1


def test_ensure_chrome_autostarts(tab_pool, monkeypatch, tmp_path):
    script = tmp_path / "start-debug-chrome.sh"
    script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    state = {"up": False}  # launcher brings Chrome up (HTTP + healthy CDP)
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        state["up"] = True
        return mock.Mock(returncode=0, stderr="")

    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: state["up"])
    monkeypatch.setattr(tab_pool, "cdp_ws_healthy", lambda timeout=3.0: state["up"])
    monkeypatch.setattr(tab_pool, "_launcher_script", lambda: script)
    monkeypatch.setattr(tab_pool.subprocess, "run", fake_run)

    tab_pool.ensure_chrome_running()
    assert runs and runs[0][0] == "bash"
    assert runs[0][1] == str(script)
    assert runs[0][2] == "start"  # cold start, not a restart


def test_ensure_chrome_restarts_when_cdp_degraded(tab_pool, monkeypatch, tmp_path):
    """HTTP up but CDP WebSocket unhealthy → force a launcher *restart*.

    Regression for POVISON 694: a long-lived Chrome answered /json/version
    while every WS upgrade 500'd, so the pool opened blank tabs that could
    never attach and the run hung on an empty tab.
    """
    script = tmp_path / "start-debug-chrome.sh"
    script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    state = {"healthy": False}  # HTTP always up; CDP recovers only after restart
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        state["healthy"] = True
        return mock.Mock(returncode=0, stderr="")

    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "cdp_ws_healthy", lambda timeout=3.0: state["healthy"])
    monkeypatch.setattr(tab_pool, "_launcher_script", lambda: script)
    monkeypatch.setattr(tab_pool.subprocess, "run", fake_run)

    tab_pool.ensure_chrome_running()
    assert runs and runs[0][2] == "restart"


def test_cdp_ws_healthy_false_when_socket_unreachable(tab_pool, monkeypatch):
    # /json/version resolves but the advertised ws port is closed → unhealthy.
    monkeypatch.setattr(
        tab_pool,
        "_http_json",
        lambda url, *, method="GET", timeout=10.0: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/DEAD"
        },
    )
    assert tab_pool.cdp_ws_healthy(timeout=0.5) is False
