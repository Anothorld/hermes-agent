"""Tests for LAN upload link base URL resolution."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_lan_test"


def _load(sub: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


def test_default_vault_public_base_uses_lan_ip(monkeypatch):
    lan = _load("bridge_lan")
    vault = _load("escalation_attachment_vault")
    monkeypatch.delenv("CS_OPS_ESC_VAULT_PUBLIC_BASE", raising=False)
    monkeypatch.setenv("CSCS_BRIDGE_PORT", "8081")
    monkeypatch.setattr(lan, "local_lan_ipv4", lambda: "192.168.1.42")
    assert vault.public_base_url() == "http://192.168.1.42:8081/api/plugins/cs-ops-bridge"


def test_public_base_url_honors_explicit_env(monkeypatch):
    vault = _load("escalation_attachment_vault")
    monkeypatch.setenv(
        "CS_OPS_ESC_VAULT_PUBLIC_BASE",
        "http://10.0.0.5:9090/api/plugins/cs-ops-bridge",
    )
    assert vault.public_base_url() == "http://10.0.0.5:9090/api/plugins/cs-ops-bridge"


def test_local_lan_ipv4_prefers_udp_route(monkeypatch):
    lan = _load("bridge_lan")

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, _addr):
            return None

        def getsockname(self):
            return ("192.168.88.10", 54321)

    monkeypatch.setattr(lan.socket, "socket", lambda *a, **k: FakeSock())
    assert lan.local_lan_ipv4() == "192.168.88.10"
