"""Tests for canonical povison-cs profile resolution."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_profile_test"


def _load():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.profile_refs"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "profile_refs.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_default_profile_name(monkeypatch):
    monkeypatch.delenv("CS_OPS_PROFILE", raising=False)
    monkeypatch.delenv("CS_OPS_PROFILE_DIR", raising=False)
    mod = _load()
    assert mod.cs_profile_name() == "povison-cs"
    assert mod.gateway_session_id(env="LIVE", quickcep_session_id="123") == "povison-cs:LIVE:123"


def test_profile_dir_default_layout(monkeypatch):
    monkeypatch.delenv("CS_OPS_PROFILE", raising=False)
    monkeypatch.delenv("CS_OPS_PROFILE_DIR", raising=False)
    mod = _load()
    assert mod.cs_profile_dir().name == "povison-cs"
    assert mod.cs_profile_dir().parent.name == "profiles"


def test_profile_override_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CS_OPS_PROFILE", "povison-cs-test")
    monkeypatch.setenv("CS_OPS_PROFILE_DIR", str(tmp_path / "custom-home"))
    mod = _load()
    assert mod.cs_profile_name() == "povison-cs-test"
    assert mod.cs_profile_dir() == tmp_path / "custom-home"
    assert mod.gateway_session_id(env="LIVE", quickcep_session_id="99") == "povison-cs-test:LIVE:99"


def test_profile_dir_uses_hermes_home_when_agent_sandbox_home(monkeypatch, tmp_path):
    """Agent runs set HOME to profile home/ but HERMES_HOME to the profile root."""
    profile_root = tmp_path / ".hermes" / "profiles" / "povison-cs"
    sandbox_home = profile_root / "home"
    sandbox_home.mkdir(parents=True)
    cli = profile_root / "skills" / "social-media" / "quickcep" / "scripts" / "quickcep_cli.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("# stub", encoding="utf-8")

    monkeypatch.delenv("CS_OPS_PROFILE_DIR", raising=False)
    monkeypatch.delenv("CS_OPS_PROFILE", raising=False)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("HERMES_HOME", str(profile_root))

    mod = _load()
    assert mod.cs_profile_dir() == profile_root
    assert mod.quickcep_skill_dir() == profile_root / "skills" / "social-media" / "quickcep"
    assert mod.quickcep_skill_dir().joinpath("scripts", "quickcep_cli.py").is_file()


def test_profile_dir_from_root_hermes_home(monkeypatch, tmp_path):
    hermes_root = tmp_path / ".hermes"
    profile_root = hermes_root / "profiles" / "povison-cs"
    profile_root.mkdir(parents=True)

    monkeypatch.delenv("CS_OPS_PROFILE_DIR", raising=False)
    monkeypatch.delenv("CS_OPS_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_root))

    mod = _load()
    assert mod.cs_profile_dir() == profile_root
