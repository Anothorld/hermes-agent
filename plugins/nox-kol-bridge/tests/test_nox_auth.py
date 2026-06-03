"""Tests for Nox auth hydration and bootstrap."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal import nox_auth  # noqa: E402


@pytest.fixture
def isolated_nox_config(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".noxinfluencer"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.json"
    monkeypatch.setenv("NOXINFLUENCER_CONFIG", str(cfg_path))
    return cfg_path


@pytest.fixture
def hermes_profile(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_resolve_env_api_key_from_hermes_dotenv(hermes_profile, monkeypatch):
    monkeypatch.delenv("NOXINFLUENCER_API_KEY", raising=False)
    (hermes_profile / ".env").write_text(
        "NOXINFLUENCER_API_KEY=secret-from-file\n",
        encoding="utf-8",
    )
    assert nox_auth.resolve_env_api_key() == "secret-from-file"
    assert nox_auth.resolve_env_api_key(hydrate=False) == "secret-from-file"


def test_has_stored_api_key_false_when_missing(isolated_nox_config):
    assert nox_auth.has_stored_api_key() is False


def test_has_stored_api_key_true_when_present(isolated_nox_config):
    isolated_nox_config.write_text(
        json.dumps({"api_key": "stored-key"}),
        encoding="utf-8",
    )
    assert nox_auth.has_stored_api_key() is True


def test_bootstrap_auth_from_env_writes_config(
    isolated_nox_config, monkeypatch,
):
    monkeypatch.setenv("NOXINFLUENCER_API_KEY", "bootstrap-me")

    def _fake_run(cmd, **kwargs):
        assert cmd[-2:] == ["auth", "--key-stdin"]
        isolated_nox_config.write_text(
            json.dumps({"api_key": "bootstrap-me"}),
            encoding="utf-8",
        )
        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr(nox_auth.shutil, "which", lambda _: "/usr/bin/noxinfluencer")
    monkeypatch.setattr(nox_auth.subprocess, "run", _fake_run)
    assert nox_auth.bootstrap_auth_from_env() is True
    assert nox_auth.has_stored_api_key() is True


def test_ensure_nox_auth_raises_when_no_key(
    isolated_nox_config, hermes_profile, monkeypatch,
):
    monkeypatch.delenv("NOXINFLUENCER_API_KEY", raising=False)
    monkeypatch.setattr(nox_auth.shutil, "which", lambda _: "/usr/bin/noxinfluencer")
    monkeypatch.setattr(nox_auth, "_candidate_hermes_env_files", lambda: [])
    with pytest.raises(nox_auth.NoxAuthError, match="Not authenticated"):
        nox_auth.ensure_nox_auth("LIVE")


def test_classify_auth_error():
    assert nox_auth.classify_auth_error("Not authenticated.") == "NOX_AUTH_MISSING"
    assert nox_auth.classify_auth_error("AUTH_REQUIRED") == "NOX_AUTH_MISSING"
    assert nox_auth.classify_auth_error("timeout") is None


def test_auth_status_test_ok():
    out = nox_auth.auth_status(env="TEST")
    assert out["ok"] is True
    assert out["mode"] == "TEST_fixtures"
