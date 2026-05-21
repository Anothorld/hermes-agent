"""Tests for plugins.kol_ops_bridge.notifier."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def notifier(monkeypatch):
    plugin_root = Path(__file__).resolve().parents[1]
    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg
    full_name = f"{pkg_name}.notifier"
    spec = importlib.util.spec_from_file_location(
        full_name, plugin_root / "notifier.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.delenv("HERMES_DINGTALK_WEBHOOK", raising=False)
    monkeypatch.delenv("HERMES_DINGTALK_SECRET", raising=False)
    monkeypatch.delenv("HERMES_KOL_CONSOLE_BASE_URL", raising=False)
    return mod


def test_no_webhook_returns_silent_skip(notifier):
    result = notifier.notify(kind="info", title="x", lines=["a"])
    assert result == {"sent": False, "reason": "webhook_not_configured"}


def test_unknown_kind_raises(notifier):
    with pytest.raises(notifier.NotifierError):
        notifier.notify(kind="bogus", title="x", lines=["a"])


def test_lines_must_be_list_of_str(notifier):
    with pytest.raises(notifier.NotifierError):
        notifier.notify(kind="info", title="x", lines=["a", 42])  # type: ignore[list-item]


def test_console_link_builds_for_each_kind(notifier, monkeypatch):
    monkeypatch.setenv("HERMES_KOL_CONSOLE_BASE_URL", "https://ops.local/")
    assert notifier._console_link("escalation", {"escalation_id": 7}) == (
        "https://ops.local/escalations/7"
    )
    assert notifier._console_link("approval", {"approval_id": 9}) == (
        "https://ops.local/approvals/9"
    )
    link = notifier._console_link(
        "draft_ready", {"identity_id": 42, "campaign_id": "TS8319"}
    )
    assert link == "https://ops.local/identities/42?campaign=TS8319"


def test_signed_url_appends_signature(notifier):
    url = notifier._signed_url("https://oapi.example.com/robot/send", "topsecret")
    assert "timestamp=" in url and "sign=" in url


def test_signed_url_no_secret_passthrough(notifier):
    url = notifier._signed_url("https://oapi.example.com/robot/send", None)
    assert url == "https://oapi.example.com/robot/send"


def test_transport_failure_does_not_raise(notifier, monkeypatch):
    monkeypatch.setenv("HERMES_DINGTALK_WEBHOOK", "https://invalid.invalid/x")

    def boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(notifier, "_post", boom)
    result = notifier.notify(kind="info", title="x", lines=["a"])
    assert result["sent"] is False
    assert result["reason"] == "transport_error"
