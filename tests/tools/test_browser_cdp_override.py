from unittest.mock import Mock, patch


HOST = "example-host"
PORT = 9223
WS_URL = f"ws://{HOST}:{PORT}/devtools/browser/abc123"
HTTP_URL = f"http://{HOST}:{PORT}"
VERSION_URL = f"{HTTP_URL}/json/version"


class TestResolveCdpOverride:
    def test_keeps_full_devtools_websocket_url(self):
        from tools.browser_tool import _resolve_cdp_override

        assert _resolve_cdp_override(WS_URL) == WS_URL

    def test_resolves_http_discovery_endpoint_to_websocket(self):
        from tools.browser_tool import _resolve_cdp_override

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = _resolve_cdp_override(HTTP_URL)

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_resolves_bare_ws_hostport_to_discovery_websocket(self):
        from tools.browser_tool import _resolve_cdp_override

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = _resolve_cdp_override(f"ws://{HOST}:{PORT}")

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_falls_back_to_raw_url_when_discovery_fails(self):
        from tools.browser_tool import _resolve_cdp_override

        with patch("tools.browser_tool.requests.get", side_effect=RuntimeError("boom")):
            assert _resolve_cdp_override(HTTP_URL) == HTTP_URL

    def test_normalizes_provider_returned_http_cdp_url_when_creating_session(self, monkeypatch):
        import tools.browser_tool as browser_tool

        provider = Mock()
        provider.create_session.return_value = {
            "session_name": "cloud-session",
            "bb_session_id": "bu_123",
            "cdp_url": "https://cdp.browser-use.example/session",
            "features": {"browser_use": True},
        }

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        monkeypatch.setattr(browser_tool, "_active_sessions", {})
        monkeypatch.setattr(browser_tool, "_session_last_activity", {})
        monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr(browser_tool, "_update_session_activity", lambda task_id: None)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            session_info = browser_tool._get_session_info("task-browser-use")

        assert session_info["cdp_url"] == WS_URL
        provider.create_session.assert_called_once_with(
            "task-browser-use", session_options=None
        )
        mock_get.assert_called_once_with(
            "https://cdp.browser-use.example/session/json/version",
            timeout=10,
        )


class TestGetCdpOverride:
    def test_prefers_env_var_over_config(self, monkeypatch):
        import tools.browser_tool as browser_tool

        monkeypatch.setenv("BROWSER_CDP_URL", HTTP_URL)
        monkeypatch.setattr(
            browser_tool,
            "read_raw_config",
            lambda: {"browser": {"cdp_url": "http://config-host:9222"}},
            raising=False,
        )

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = browser_tool._get_cdp_override()

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)

    def test_uses_config_browser_cdp_url_when_env_missing(self, monkeypatch):
        import tools.browser_tool as browser_tool

        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"webSocketDebuggerUrl": WS_URL}

        with patch("hermes_cli.config.read_raw_config", return_value={"browser": {"cdp_url": HTTP_URL}}), \
             patch("tools.browser_tool.requests.get", return_value=response) as mock_get:
            resolved = browser_tool._get_cdp_override()

        assert resolved == WS_URL
        mock_get.assert_called_once_with(VERSION_URL, timeout=10)


class TestBrowserAvailabilityWithTabPool:
    """Regression (POVISON 701): in local mode the whole browser_* toolset was
    dropped from the model's tool list because ``check_browser_requirements``
    only recognised the agent-browser-bundled Chromium or BROWSER_CDP_URL — not
    the local-chrome-tab-pool, which seeds a real debug Chrome at call time.
    The model then reported 'I don't have browser_navigate' and fell back to
    terminal scraping."""

    def _local_mode(self, browser_tool, monkeypatch):
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda: "agent-browser")
        monkeypatch.setattr(
            browser_tool, "_requires_real_termux_browser_install", lambda cmd: False
        )
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)
        monkeypatch.setattr(browser_tool, "_using_lightpanda_engine", lambda: False)
        # No bundled Chromium and no live debug Chrome — the tab pool is the
        # only browser path.
        monkeypatch.setattr(browser_tool, "_chromium_installed", lambda: False)
        monkeypatch.setattr(browser_tool, "_probe_local_cdp", lambda *a, **k: None)

    def test_available_when_launcher_present_and_env_unset(self, monkeypatch):
        import tools.browser_tool as browser_tool

        self._local_mode(browser_tool, monkeypatch)
        monkeypatch.delenv("LOCAL_CHROME_TAB_POOL", raising=False)
        monkeypatch.setattr(
            browser_tool, "_locate_local_chrome_launcher", lambda: __file__
        )
        assert browser_tool.check_browser_requirements() is True

    def test_unavailable_when_pool_disabled_and_no_live_chrome(self, monkeypatch):
        import tools.browser_tool as browser_tool

        self._local_mode(browser_tool, monkeypatch)
        monkeypatch.setenv("LOCAL_CHROME_TAB_POOL", "0")
        monkeypatch.setattr(
            browser_tool, "_locate_local_chrome_launcher", lambda: __file__
        )
        assert browser_tool.check_browser_requirements() is False

    def test_available_when_live_debug_chrome_even_if_pool_disabled(self, monkeypatch):
        import tools.browser_tool as browser_tool

        self._local_mode(browser_tool, monkeypatch)
        monkeypatch.setenv("LOCAL_CHROME_TAB_POOL", "0")
        monkeypatch.setattr(
            browser_tool, "_probe_local_cdp", lambda *a, **k: WS_URL
        )
        assert browser_tool.check_browser_requirements() is True

    def test_unavailable_when_no_launcher_no_chromium(self, monkeypatch):
        import tools.browser_tool as browser_tool

        self._local_mode(browser_tool, monkeypatch)
        monkeypatch.delenv("LOCAL_CHROME_TAB_POOL", raising=False)
        monkeypatch.setattr(browser_tool, "_locate_local_chrome_launcher", lambda: None)
        assert browser_tool.check_browser_requirements() is False
