"""Hard-timeout guard for the cloud browser provider's ``create_session``.

A cloud provider whose API is unreachable (e.g. US Browser-Use cloud from a
network that cannot reach it) used to block the whole agent run forever — the
"opens a blank tab then hangs" symptom, since the hang is *before* any
agent-browser subprocess spawns. ``_create_cloud_session_with_timeout`` caps
that network call so the caller falls back to local Chrome instead of hanging.
"""

from __future__ import annotations

import time

import pytest

from tools import browser_tool


class _FastProvider:
    def create_session(self, task_id, session_options=None):
        return {"session_name": "ok", "bb_session_id": "bb1", "cdp_url": None}


class _HangingProvider:
    def __init__(self) -> None:
        self.started = False

    def create_session(self, task_id, session_options=None):
        self.started = True
        time.sleep(30)  # simulate an unreachable cloud API
        return {"session_name": "never"}


def test_returns_session_when_provider_is_fast():
    result = browser_tool._create_cloud_session_with_timeout(
        _FastProvider(), "task-1", None, timeout_s=5
    )
    assert result["session_name"] == "ok"


def test_raises_timeout_without_blocking_for_full_hang():
    provider = _HangingProvider()
    start = time.time()
    with pytest.raises(TimeoutError):
        browser_tool._create_cloud_session_with_timeout(
            provider, "task-2", None, timeout_s=1
        )
    elapsed = time.time() - start
    assert provider.started is True
    # Must abandon near the 1s deadline, NOT wait out the 30s hang.
    assert elapsed < 5, f"timeout wrapper blocked too long: {elapsed:.1f}s"


def test_timeout_config_default_and_floor(monkeypatch):
    # Unreadable config → default.
    monkeypatch.setattr(
        browser_tool, "cfg_get", lambda *a, **k: None, raising=True
    )
    assert browser_tool._cloud_session_timeout() == browser_tool._DEFAULT_CLOUD_SESSION_TIMEOUT_S
    # Configured value is honored but floored at 5s.
    monkeypatch.setattr(browser_tool, "cfg_get", lambda *a, **k: 2, raising=True)
    assert browser_tool._cloud_session_timeout() == 5
    monkeypatch.setattr(browser_tool, "cfg_get", lambda *a, **k: 45, raising=True)
    assert browser_tool._cloud_session_timeout() == 45
