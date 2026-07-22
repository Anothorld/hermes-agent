"""Tests for email-only channel gate."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_email_test"


def _load():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.email_channel"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "email_channel.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_is_email_channel():
    ec = _load()
    assert ec.is_email_channel("email")
    assert ec.is_email_channel("EMAIL")
    assert not ec.is_email_channel("web")
    assert not ec.is_email_channel("sms")
    assert not ec.is_email_channel("")


def test_inbound_payload_is_email():
    ec = _load()
    assert ec.inbound_payload_is_email({"channel": "email"})
    assert not ec.inbound_payload_is_email({"channel": "web"})
    assert not ec.inbound_payload_is_email({})
    assert not ec.inbound_payload_is_email({"channel": ""})


def test_session_is_email_uses_api_row():
    ec = _load()
    with patch.object(ec, "fetch_email_session_row", return_value={"channel": "email", "id": "s1"}):
        assert ec.session_is_email("s1")
    with patch.object(ec, "fetch_email_session_row", return_value={"channel": "web", "id": "s2"}):
        assert not ec.session_is_email("s2")
    with patch.object(ec, "fetch_email_session_row", return_value=None):
        assert not ec.session_is_email("missing")


def test_cal_session_is_email():
    ec = _load()
    assert ec.cal_session_is_email({"customer_email": "a@b.com", "status": "failed"})
    assert not ec.cal_session_is_email({"customer_email": "a@b.com", "status": "skipped"})
    assert not ec.cal_session_is_email({"customer_email": "", "status": "processing"})
    assert not ec.cal_session_is_email(None)


def test_session_is_email_cal_fallback_skips_list():
    ec = _load()
    cal = {"customer_email": "aged@example.com", "status": "failed"}
    with patch.object(ec, "fetch_email_session_row") as mock_fetch:
        assert ec.session_is_email("old-session", cal_session=cal)
        mock_fetch.assert_not_called()


def test_session_is_email_messages_fallback():
    ec = _load()
    with patch.object(ec, "fetch_email_session_row", return_value=None), \
         patch.object(ec, "_session_has_email_messages", return_value=True):
        assert ec.session_is_email("old-session")
