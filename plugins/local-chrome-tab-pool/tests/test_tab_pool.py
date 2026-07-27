"""Tests for local-chrome tab pool (canonical internal module)."""

from __future__ import annotations

import importlib.util
import time
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
    module._orphan_first_seen.clear()
    return module


def test_is_enabled_default_on(tab_pool, monkeypatch):
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    assert tab_pool.is_enabled() is True


def test_is_enabled_can_disable(tab_pool, monkeypatch):
    monkeypatch.setenv("LOCAL_CHROME_TAB_POOL", "0")
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    assert tab_pool.is_enabled() is False


def test_is_enabled_stays_on_with_browser_level_cdp(tab_pool, monkeypatch):
    monkeypatch.delenv("LOCAL_CHROME_TAB_POOL", raising=False)
    monkeypatch.delenv("LOCAL_CHROME_FORCE_SHARED_CDP", raising=False)
    # start-debug-chrome.sh writes browser-level ws — pool still active.
    monkeypatch.setenv(
        "BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/abc-123",
    )
    assert tab_pool.is_enabled() is True
    monkeypatch.setenv("LOCAL_CHROME_FORCE_SHARED_CDP", "1")
    assert tab_pool.is_enabled() is False


def test_normalize_task_id_strips_sidecar_suffix(tab_pool):
    assert tab_pool.normalize_task_id("abc::local") == "abc"
    assert tab_pool.normalize_task_id("abc") == "abc"
    assert tab_pool.normalize_task_id(None) == "default"


def test_release_invokes_http_close_target(tab_pool, monkeypatch):
    closed = []

    def fake_close(url, *, timeout=5.0):
        closed.append(url)

    monkeypatch.setattr(tab_pool, "_http_close_target", fake_close)
    tab_pool._task_tabs["task-a"] = {
        "target_id": "TAB99",
        "cdp_url": "ws://127.0.0.1/devtools/page/TAB99",
    }
    assert tab_pool.release("task-a") is True
    assert any("/json/close/TAB99" in u for u in closed)


def test_http_close_target_accepts_empty_body(tab_pool, monkeypatch):
    class _Resp:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(tab_pool.urllib.request, "urlopen", lambda *a, **k: _Resp())
    tab_pool._http_close_target("http://127.0.0.1:9222/json/close/ABC", timeout=1.0)


def test_http_close_target_accepts_plain_text_body(tab_pool, monkeypatch):
    class _Resp:
        def read(self):
            return b"Target is closing"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(tab_pool.urllib.request, "urlopen", lambda *a, **k: _Resp())
    tab_pool._http_close_target("http://127.0.0.1:9222/json/close/ABC", timeout=1.0)


def test_acquire_creates_tab(tab_pool, monkeypatch):
    calls = []

    def fake_http_json(url, *, method="GET", timeout=10.0):
        calls.append((url, method))
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/ABC"}
        if url.endswith("/json/list"):
            # Cached tab liveness check — report TAB1 as live.
            return [{"type": "page", "id": "TAB1", "url": "https://example.com/cur"}]
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
    monkeypatch.setattr(tab_pool, "reap_orphan_blank_tabs", lambda: 0)
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
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://x"}
        raise AssertionError(url)

    def fake_close(url, *, timeout=5.0):
        if "/json/close/TAB9" in url:
            closed.append(url)

    monkeypatch.setattr(tab_pool, "_http_json", fake_http_json)
    monkeypatch.setattr(tab_pool, "_http_close_target", fake_close)
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

    def fake_close(url, *, timeout=5.0):
        if "/json/close/T1" in url:
            closed.append(url)

    monkeypatch.setattr(tab_pool, "_http_close_target", fake_close)
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


def test_reap_orphan_tabs_real_url_age_gated(tab_pool, monkeypatch):
    # Real-URL orphans must NOT be closed on first sight — a concurrent run
    # might have just opened the tab and be mid-navigate. They are recorded
    # and only reaped once observed as orphan for >= the age threshold.
    tab_pool._orphan_first_seen.clear()
    listing = [
        {"type": "page", "id": "REAL1", "url": "https://www.google.com/search?q=x"},
        {"type": "page", "id": "REAL2", "url": "https://www.instagram.com/foo/"},
    ]
    closed = []
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: closed.append(tid))
    monkeypatch.setattr(
        tab_pool, "_http_json",
        lambda url, *, method="GET", timeout=10.0: listing,
    )

    # First pass: record only, close nothing.
    assert tab_pool.reap_orphan_tabs() == 0
    assert closed == []
    assert set(tab_pool._orphan_first_seen.keys()) == {"REAL1", "REAL2"}

    # Second pass immediately after: still below threshold → still kept.
    assert tab_pool.reap_orphan_tabs() == 0
    assert closed == []


def test_reap_orphan_tabs_real_url_closed_after_age_threshold(tab_pool, monkeypatch):
    tab_pool._orphan_first_seen.clear()
    listing = [{"type": "page", "id": "STALE", "url": "https://www.instagram.com/foo/"}]
    closed = []
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: closed.append(tid))
    monkeypatch.setattr(
        tab_pool, "_http_json",
        lambda url, *, method="GET", timeout=10.0: listing,
    )

    # First pass records the orphan.
    assert tab_pool.reap_orphan_tabs() == 0
    # Backdate the first-seen timestamp so the next pass exceeds the threshold.
    tab_pool._orphan_first_seen["STALE"] -= tab_pool._ORPHAN_REAL_URL_AGE_S + 1.0

    # Second pass now reaps it.
    assert tab_pool.reap_orphan_tabs() == 1
    assert closed == ["STALE"]
    # Map entry dropped after close.
    assert "STALE" not in tab_pool._orphan_first_seen


def test_reap_orphan_tabs_drops_first_seen_for_evicted_targets(tab_pool, monkeypatch):
    # If Chrome evicted a tab (or we closed it), its id vanishes from /json/list.
    # The first-seen map must not grow unbounded.
    tab_pool._orphan_first_seen.clear()
    tab_pool._orphan_first_seen["GONE"] = 1.0
    # STILL_HERE is in the listing and freshly seen → must be preserved.
    tab_pool._orphan_first_seen["STILL_HERE"] = time.monotonic()
    listing = [{"type": "page", "id": "STILL_HERE", "url": "https://example.com/a"}]
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: None)
    monkeypatch.setattr(
        tab_pool, "_http_json",
        lambda url, *, method="GET", timeout=10.0: listing,
    )

    tab_pool.reap_orphan_tabs()
    assert "GONE" not in tab_pool._orphan_first_seen
    assert "STILL_HERE" in tab_pool._orphan_first_seen


def test_reap_orphan_tabs_tracked_real_url_preserved(tab_pool, monkeypatch):
    # A live pooled tab on a real URL must never be reaped even after age.
    tab_pool._task_tabs["live"] = {
        "target_id": "TRACKED_REAL",
        "cdp_url": "ws://127.0.0.1:9222/devtools/page/TRACKED_REAL",
    }
    tab_pool._orphan_first_seen.clear()
    listing = [{"type": "page", "id": "TRACKED_REAL", "url": "https://www.instagram.com/live/"}]
    closed = []
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: closed.append(tid))
    monkeypatch.setattr(
        tab_pool, "_http_json",
        lambda url, *, method="GET", timeout=10.0: listing,
    )

    # Backdate well past threshold — tracked tab still preserved.
    tab_pool.reap_orphan_tabs()
    tab_pool.reap_orphan_tabs()
    assert closed == []


def test_acquire_evicts_stale_cached_tab_after_chrome_restart(tab_pool, monkeypatch):
    # After Chrome restart, the cached target_id is gone from /json/list.
    # acquire must evict the stale entry and create a fresh tab instead of
    # returning a dead cdp_url (which would make RPA navigate hit HTTP 500).
    tab_pool._task_tabs.clear()
    tab_pool._task_tabs["stale-task"] = {
        "target_id": "DEAD_TARGET",
        "cdp_url": "ws://127.0.0.1:9222/devtools/page/DEAD_TARGET",
    }
    # get_target_url returns "" for the dead target (not in listing).
    monkeypatch.setattr(tab_pool, "get_target_url", lambda tid: "")
    create_calls = []

    def fake_create():
        create_calls.append(1)
        return {
            "target_id": "FRESH_TARGET",
            "cdp_url": "ws://127.0.0.1:9222/devtools/page/FRESH_TARGET",
        }

    monkeypatch.setattr(tab_pool, "_create_tab", fake_create)
    monkeypatch.setattr(tab_pool, "maybe_probe_cdp_health", lambda **kw: None)
    monkeypatch.setattr(tab_pool, "reap_orphan_blank_tabs", lambda: 0)
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: None)

    info = tab_pool.acquire("stale-task")
    assert info["target_id"] == "FRESH_TARGET"
    assert create_calls == [1]
    # Stale entry replaced with the fresh one.
    assert tab_pool._task_tabs["stale-task"]["target_id"] == "FRESH_TARGET"


def test_acquire_reuses_cached_tab_when_target_still_live(tab_pool, monkeypatch):
    # When the cached target_id still exists in Chrome, acquire must reuse it
    # (no unnecessary tab churn).
    tab_pool._task_tabs.clear()
    tab_pool._task_tabs["live-task"] = {
        "target_id": "ALIVE",
        "cdp_url": "ws://127.0.0.1:9222/devtools/page/ALIVE",
    }
    monkeypatch.setattr(tab_pool, "get_target_url", lambda tid: "https://example.com/current")
    monkeypatch.setattr(tab_pool, "_create_tab", lambda: pytest.fail("must not create"))
    monkeypatch.setattr(tab_pool, "maybe_probe_cdp_health", lambda **kw: None)
    monkeypatch.setattr(tab_pool, "reap_orphan_blank_tabs", lambda: 0)

    info = tab_pool.acquire("live-task")
    assert info["target_id"] == "ALIVE"



    # After a Chrome restart all target_ids are gone; the age-gate map must
    # reset so recycled ids aren't reaped instantly post-recovery.
    tab_pool._orphan_first_seen.clear()
    tab_pool._orphan_first_seen["OLD"] = 1.0
    tab_pool._task_tabs.clear()
    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: None)
    monkeypatch.setattr(tab_pool, "ensure_chrome_running", lambda: None)

    tab_pool.recover_degraded_chrome()
    assert tab_pool._orphan_first_seen == {}



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


def test_maybe_probe_cdp_health_restarts_when_degraded(tab_pool, monkeypatch):
    state = {"healthy": False, "recover_calls": 0}

    monkeypatch.setattr(tab_pool, "is_enabled", lambda: True)
    monkeypatch.setattr(tab_pool, "probe_chrome", lambda: True)
    monkeypatch.setattr(
        tab_pool,
        "cdp_ws_healthy",
        lambda timeout=3.0: state["healthy"],
    )

    def fake_recover(task_id=None):
        state["recover_calls"] += 1
        state["healthy"] = True

    monkeypatch.setattr(tab_pool, "recover_degraded_chrome", fake_recover)
    tab_pool._last_cdp_probe_at = 0.0

    assert tab_pool.maybe_probe_cdp_health(force=True) is True
    assert state["recover_calls"] == 1


def test_recover_degraded_chrome_clears_task_tab(tab_pool, monkeypatch):
    closed: list[str] = []

    monkeypatch.setattr(tab_pool, "_close_target_id", lambda tid: closed.append(tid))
    monkeypatch.setattr(tab_pool, "ensure_chrome_running", lambda: None)
    tab_pool._task_tabs["task-a"] = {
        "target_id": "TAB-A",
        "cdp_url": "ws://127.0.0.1/devtools/page/TAB-A",
    }

    tab_pool.recover_degraded_chrome("task-a")

    assert "task-a" not in tab_pool._task_tabs
    assert closed == ["TAB-A"]
