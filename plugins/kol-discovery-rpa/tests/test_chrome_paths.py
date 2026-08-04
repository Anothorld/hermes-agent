"""Tests for shared debug-Chrome cookie path resolution."""

from __future__ import annotations

import sys
from pathlib import Path

_INTERNAL = Path(__file__).resolve().parents[1] / "internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

import chrome_paths  # noqa: E402


def test_default_profile_uses_home_shared_not_hermes_home(monkeypatch, tmp_path):
    """kol-orchestrator HERMES_HOME must not nest the shared chrome profile."""
    monkeypatch.delenv("DEBUG_CHROME_PROFILE_DIR", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "kol-orchestrator"))

    profile = chrome_paths.default_chrome_profile_dir()
    assert profile == str(fake_home / ".hermes" / "local-chrome-debug-profile")
    assert "kol-orchestrator" not in profile


def test_resolve_cookie_db_prefers_existing_shared(monkeypatch, tmp_path):
    monkeypatch.delenv("DEBUG_CHROME_PROFILE_DIR", raising=False)
    fake_home = tmp_path / "home"
    shared = fake_home / ".hermes" / "local-chrome-debug-profile" / "Default"
    shared.mkdir(parents=True)
    cookie = shared / "Cookies"
    cookie.write_bytes(b"x")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv(
        "HERMES_HOME",
        str(tmp_path / "profiles" / "kol-orchestrator"),
    )

    resolved = chrome_paths.resolve_chrome_cookie_db()
    assert resolved == cookie


def test_resolve_cookie_db_honors_env_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-chrome"
    cookies = override / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"x")
    monkeypatch.setenv("DEBUG_CHROME_PROFILE_DIR", str(override))

    assert chrome_paths.default_chrome_profile_dir() == str(override)
    assert chrome_paths.resolve_chrome_cookie_db() == cookies


def test_resolve_cookie_db_fallback_message_path(monkeypatch, tmp_path):
    """When nothing exists, return the preferred shared path for errors."""
    monkeypatch.delenv("DEBUG_CHROME_PROFILE_DIR", raising=False)
    fake_home = tmp_path / "empty-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "kol"))

    resolved = chrome_paths.resolve_chrome_cookie_db()
    assert resolved == (
        fake_home / ".hermes" / "local-chrome-debug-profile" / "Default" / "Cookies"
    )
    assert not resolved.exists()
