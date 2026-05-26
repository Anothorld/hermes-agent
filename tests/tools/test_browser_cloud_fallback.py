"""Tests for cloud browser provider runtime fallback to CDP local Chrome.

Covers the fallback chain in ``_get_session_info`` when a cloud provider
fails at runtime:

  cloud → existing CDP override → probe 127.0.0.1:9222 →
    auto-launch debug Chrome → hard-fail

Headless agent-browser Chromium is intentionally not in the fallback path;
sites with bot detection require the real-Chrome route.
"""
import logging
from unittest.mock import Mock, patch

import pytest

import tools.browser_tool as browser_tool


def _reset_session_state(monkeypatch):
    """Clear caches so each test starts fresh."""
    monkeypatch.setattr(browser_tool, "_active_sessions", {})
    monkeypatch.setattr(browser_tool, "_cached_cloud_provider", None)
    monkeypatch.setattr(browser_tool, "_cloud_provider_resolved", False)
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(browser_tool, "_update_session_activity", lambda t: None)
    # Tests opt into BROWSER_CDP_URL explicitly via _get_cdp_override mock.
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)


class TestCloudProviderRuntimeFallback:
    """Tests for _get_session_info cloud → CDP fallback chain."""

    def test_cloud_failure_falls_back_to_probed_cdp(self, monkeypatch):
        """When cloud fails AND a debug Chrome is running, route via CDP."""
        _reset_session_state(monkeypatch)

        provider = Mock()
        provider.create_session.side_effect = RuntimeError("503 Service Unavailable")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp",
            lambda url=browser_tool._DEFAULT_LOCAL_CDP_PROBE_URL:
                "ws://127.0.0.1:9222/devtools/browser/abc",
        )
        autolaunch = Mock()
        monkeypatch.setattr(browser_tool, "_try_autolaunch_local_chrome", autolaunch)

        session = browser_tool._get_session_info("task-1")

        autolaunch.assert_not_called()
        assert session["fallback_from_cloud"] is True
        assert session["fallback_mode"] == "cdp"
        assert "503" in session["fallback_reason"]
        assert session["fallback_provider"] == "Mock"
        assert session["cdp_url"] == "ws://127.0.0.1:9222/devtools/browser/abc"
        assert session["features"]["cdp_override"] is True

    def test_cloud_failure_triggers_autolaunch_when_probe_misses(self, monkeypatch):
        """No debug Chrome → autolaunch script → use returned CDP URL."""
        _reset_session_state(monkeypatch)

        provider = Mock()
        provider.create_session.side_effect = RuntimeError("connection reset")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp", lambda url=None: None,
        )
        monkeypatch.setattr(
            browser_tool, "_try_autolaunch_local_chrome",
            lambda url=None: "ws://127.0.0.1:9222/devtools/browser/xyz",
        )

        session = browser_tool._get_session_info("task-2")

        assert session["fallback_from_cloud"] is True
        assert session["fallback_mode"] == "cdp_autostart"
        assert session["cdp_url"] == "ws://127.0.0.1:9222/devtools/browser/xyz"
        # Auto-launch persists BROWSER_CDP_URL so subsequent tasks short-circuit
        # to CDP without re-trying the dead cloud.
        import os
        assert os.environ.get("BROWSER_CDP_URL") == "ws://127.0.0.1:9222/devtools/browser/xyz"

    def test_cloud_failure_hard_fails_when_autolaunch_fails(self, monkeypatch):
        """Cloud down + no probe + autolaunch failure → actionable RuntimeError."""
        _reset_session_state(monkeypatch)

        provider = Mock()
        provider.create_session.side_effect = RuntimeError("cloud boom")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
        monkeypatch.setattr(browser_tool, "_probe_local_cdp", lambda url=None: None)
        monkeypatch.setattr(
            browser_tool, "_try_autolaunch_local_chrome", lambda url=None: None,
        )

        with pytest.raises(RuntimeError) as excinfo:
            browser_tool._get_session_info("task-3")

        msg = str(excinfo.value)
        assert "cloud boom" in msg
        assert "could not be reached or auto-started" in msg
        assert "start-debug-chrome.sh" in msg

    def test_cloud_success_no_fallback(self, monkeypatch):
        """When cloud succeeds, no fallback markers are present."""
        _reset_session_state(monkeypatch)

        provider = Mock()
        provider.create_session.return_value = {
            "session_name": "cloud-sess",
            "bb_session_id": "bb_123",
            "cdp_url": None,
            "features": {"browser_use": True},
        }
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)

        session = browser_tool._get_session_info("task-4")

        assert session["session_name"] == "cloud-sess"
        assert "fallback_from_cloud" not in session
        assert "fallback_reason" not in session
        assert "fallback_mode" not in session

    def test_no_provider_uses_local_directly(self, monkeypatch):
        """When no cloud provider is configured, headless local mode is used.

        The cloud-to-CDP fallback only kicks in on provider failure; if no
        provider is configured the code never enters that branch and still
        returns the agent-browser local session.
        """
        _reset_session_state(monkeypatch)

        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)

        session = browser_tool._get_session_info("task-5")

        assert session["features"]["local"] is True
        assert "fallback_from_cloud" not in session

    def test_cdp_override_bypasses_provider(self, monkeypatch):
        """CDP override takes priority — cloud provider is never consulted."""
        _reset_session_state(monkeypatch)

        provider = Mock()
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(
            browser_tool, "_get_cdp_override",
            lambda: "ws://host:9222/devtools/browser/abc",
        )

        session = browser_tool._get_session_info("task-6")

        provider.create_session.assert_not_called()
        assert session["cdp_url"] == "ws://host:9222/devtools/browser/abc"

    def test_fallback_logs_warning_with_provider_name(self, monkeypatch, caplog):
        """Fallback emits a warning log with the provider class name and error."""
        _reset_session_state(monkeypatch)

        BrowserUseProviderFake = type("BrowserUseProvider", (), {
            "create_session": Mock(side_effect=ConnectionError("timeout")),
        })
        provider = BrowserUseProviderFake()
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp",
            lambda url=None: "ws://127.0.0.1:9222/devtools/browser/log",
        )

        with caplog.at_level(logging.WARNING, logger="tools.browser_tool"):
            session = browser_tool._get_session_info("task-7")

        assert session["fallback_from_cloud"] is True
        assert session["fallback_mode"] == "cdp"
        assert any("BrowserUseProvider" in r.message and "timeout" in r.message
                    for r in caplog.records)

    def test_cloud_failure_does_not_poison_next_task(self, monkeypatch):
        """First task falls back to CDP; second task with recovered cloud uses cloud.

        Note: in production the fallback path sets ``BROWSER_CDP_URL`` so
        subsequent tasks short-circuit to CDP without retrying the dead
        cloud. This test models the case where the operator has cleared
        that env var (or the worker doesn't run autolaunch) — the cloud
        provider is consulted fresh per task.
        """
        _reset_session_state(monkeypatch)

        call_count = 0

        def create_session_flaky(task_id, *, session_options=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            return {
                "session_name": "cloud-ok",
                "bb_session_id": "bb_999",
                "cdp_url": None,
                "features": {"browser_use": True},
            }

        provider = Mock()
        provider.create_session.side_effect = create_session_flaky
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp",
            lambda url=None: "ws://127.0.0.1:9222/devtools/browser/flaky",
        )

        s1 = browser_tool._get_session_info("task-a")
        assert s1["fallback_from_cloud"] is True
        assert s1["fallback_mode"] == "cdp"

        s2 = browser_tool._get_session_info("task-b")
        assert "fallback_from_cloud" not in s2
        assert s2["session_name"] == "cloud-ok"

    def test_cloud_returns_invalid_session_triggers_fallback(self, monkeypatch):
        """Cloud provider returning None triggers the CDP fallback chain."""
        _reset_session_state(monkeypatch)

        provider = Mock()
        provider.create_session.return_value = None
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp",
            lambda url=None: "ws://127.0.0.1:9222/devtools/browser/inv",
        )

        session = browser_tool._get_session_info("task-8")

        assert session["fallback_from_cloud"] is True
        assert session["fallback_mode"] == "cdp"
        assert "invalid session" in session["fallback_reason"]


class TestProbeLocalCDP:
    """Tests for the _probe_local_cdp helper."""

    def test_probe_returns_websocket_url_on_200(self, monkeypatch):
        response = Mock()
        response.json.return_value = {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/p1",
        }
        response.raise_for_status = Mock()
        monkeypatch.setattr(browser_tool.requests, "get", lambda *a, **kw: response)

        assert browser_tool._probe_local_cdp() == (
            "ws://127.0.0.1:9222/devtools/browser/p1"
        )

    def test_probe_returns_none_when_server_unreachable(self, monkeypatch):
        def boom(*a, **kw):
            raise browser_tool.requests.exceptions.ConnectionError("refused")
        monkeypatch.setattr(browser_tool.requests, "get", boom)

        assert browser_tool._probe_local_cdp() is None

    def test_probe_returns_none_on_missing_ws_url(self, monkeypatch):
        response = Mock()
        response.json.return_value = {"Browser": "Chrome/120"}
        response.raise_for_status = Mock()
        monkeypatch.setattr(browser_tool.requests, "get", lambda *a, **kw: response)

        assert browser_tool._probe_local_cdp() is None


class TestAutolaunchLocalChrome:
    """Tests for the _try_autolaunch_local_chrome helper."""

    def test_autolaunch_short_circuits_when_already_running(self, monkeypatch):
        """If something raced us and Chrome is up, skip the subprocess."""
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp",
            lambda url=browser_tool._DEFAULT_LOCAL_CDP_PROBE_URL:
                "ws://127.0.0.1:9222/devtools/browser/race",
        )
        spawn = Mock()
        monkeypatch.setattr(browser_tool.subprocess, "run", spawn)

        result = browser_tool._try_autolaunch_local_chrome()

        assert result == "ws://127.0.0.1:9222/devtools/browser/race"
        spawn.assert_not_called()

    def test_autolaunch_returns_none_when_script_missing(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_probe_local_cdp", lambda url=None: None)
        monkeypatch.setattr(
            browser_tool, "_locate_local_chrome_launcher", lambda: None,
        )

        assert browser_tool._try_autolaunch_local_chrome() is None

    def test_autolaunch_returns_none_on_script_failure(self, monkeypatch, tmp_path):
        script = tmp_path / "start-debug-chrome.sh"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(0o755)

        monkeypatch.setattr(browser_tool, "_probe_local_cdp", lambda url=None: None)
        monkeypatch.setattr(
            browser_tool, "_locate_local_chrome_launcher", lambda: script,
        )

        completed = Mock()
        completed.returncode = 1
        completed.stderr = "chrome not found"
        monkeypatch.setattr(
            browser_tool.subprocess, "run", lambda *a, **kw: completed,
        )

        assert browser_tool._try_autolaunch_local_chrome() is None

    def test_autolaunch_returns_url_on_success(self, monkeypatch, tmp_path):
        script = tmp_path / "start-debug-chrome.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)

        # First probe (lock-acquisition check) misses; post-launch probe succeeds.
        probe_results = iter([None, "ws://127.0.0.1:9222/devtools/browser/new"])
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp",
            lambda url=None: next(probe_results),
        )
        monkeypatch.setattr(
            browser_tool, "_locate_local_chrome_launcher", lambda: script,
        )

        completed = Mock()
        completed.returncode = 0
        completed.stderr = ""
        monkeypatch.setattr(
            browser_tool.subprocess, "run", lambda *a, **kw: completed,
        )

        assert browser_tool._try_autolaunch_local_chrome() == (
            "ws://127.0.0.1:9222/devtools/browser/new"
        )
