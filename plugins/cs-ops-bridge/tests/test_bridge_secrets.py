"""Tests for bridge_secrets key resolution."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = "bridge_secrets_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _PLUGIN_ROOT / "bridge_secrets.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_load_bridge_key_from_secrets_file(monkeypatch, tmp_path):
    mod = _load()
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("bridge_key: from-secrets-file\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_CS_OPS_BRIDGE_KEY", raising=False)
    monkeypatch.delenv("CS_OPS_BRIDGE_KEY", raising=False)
    # bridge_secrets uses expanduser ~/.hermes/... — patch path via HOME won't work
    monkeypatch.setattr(mod, "_SECRETS_PATH", secrets)
    assert mod.load_bridge_key() == "from-secrets-file"


def test_require_bridge_key_bytes_from_env(monkeypatch):
    mod = _load()
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "env-key")
    assert mod.require_bridge_key_bytes() == b"env-key"
