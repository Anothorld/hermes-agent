"""Learning LLM resolves via Hermes agent config when available."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "kol_ops_bridge_pkg"


def _load_hermes_runtime(bridge_pkg):
    mod_name = f"{_PKG_NAME}.internal.learning_hermes_runtime"
    if mod_name in pytest.importorskip("sys").modules:
        return pytest.importorskip("sys").modules[mod_name]
    import sys

    spec = importlib.util.spec_from_file_location(
        mod_name,
        _PLUGIN_ROOT / "internal" / "learning_hermes_runtime.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_explicit_learning_key_skips_hermes(bridge_pkg, monkeypatch):
    llm = bridge_pkg.learning_llm
    monkeypatch.setenv("KOL_LEARNING_LLM_API_KEY", "test-key")
    monkeypatch.delenv("KOL_LEARNING_LLM_DISABLE_HERMES", raising=False)
    hermes_rt = _load_hermes_runtime(bridge_pkg)

    def fail_hermes(*_a, **_k):
        raise AssertionError("should not call Hermes")

    monkeypatch.setattr(hermes_rt, "invoke_via_hermes_call_llm", fail_hermes)

    def fake_openai(prompt: str, **_kwargs):
        return "ok"

    monkeypatch.setattr(llm, "_invoke_openai_compatible", fake_openai)
    assert llm.invoke_learning_llm("prompt") == "ok"


def test_uses_hermes_call_llm_when_no_override(bridge_pkg, monkeypatch):
    llm = bridge_pkg.learning_llm
    monkeypatch.delenv("KOL_LEARNING_LLM_API_KEY", raising=False)
    monkeypatch.delenv("KOL_LEARNING_LLM_MODEL", raising=False)
    monkeypatch.delenv("KOL_LEARNING_LLM_API_URL", raising=False)
    monkeypatch.delenv("KOL_LEARNING_LLM_DISABLE_HERMES", raising=False)
    hermes_rt = _load_hermes_runtime(bridge_pkg)

    monkeypatch.setattr(llm, "_resolve_hermes_runtime", lambda: None)
    monkeypatch.setattr(
        llm,
        "_invoke_openai_compatible",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("openai fallback")),
    )
    monkeypatch.setattr(llm, "_openai_sdk_available", lambda: True)
    monkeypatch.setattr(
        hermes_rt,
        "invoke_via_hermes_call_llm",
        lambda prompt, **kwargs: "hermes-markdown",
    )
    import sys

    sys.modules[f"{_PKG_NAME}.internal.learning_hermes_runtime"] = hermes_rt
    assert llm.invoke_learning_llm("distill me") == "hermes-markdown"


def test_hermes_http_runtime_before_call_llm(bridge_pkg, monkeypatch):
    llm = bridge_pkg.learning_llm
    monkeypatch.delenv("KOL_LEARNING_LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        llm,
        "_resolve_hermes_runtime",
        lambda: {
            "base_url": "https://example.test/v1",
            "api_key": "k",
            "model": "m",
        },
    )
    monkeypatch.setattr(
        llm,
        "_invoke_openai_compatible",
        lambda prompt, **kw: "http-ok" if kw.get("api_key") == "k" else "bad",
    )
    monkeypatch.setattr(
        llm,
        "_openai_sdk_available",
        lambda: (_ for _ in ()).throw(AssertionError("call_llm")),
    )
    assert llm.invoke_learning_llm("x") == "http-ok"
