"""Tests for reply-draft approve: thread-based prior-message fallback."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "kol_ops_bridge_pkg"


def _load_plugin_api():
    fq = f"{_PKG}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(fq, _PLUGIN_ROOT / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def _inbound(*, msg_id: str = "MSG1", thread_id: str = "TH1"):
    return SimpleNamespace(
        message_id=msg_id,
        thread_id=thread_id,
        from_addr="kol@example.com",
        to="us@brand.com, manager@agency.com",
        cc="cc@partner.com",
        subject="Re: collab",
        snippet="",
        in_reply_to=None,
        references=None,
        date="Mon, 1 Jan 2024 12:00:00 +0000",
        body="Prior mail body here.",
    )


def test_fetch_inbound_prefers_source_message_id(cal_db):
    plugin_api = _load_plugin_api()
    client = MagicMock()
    client.get_message.return_value = _inbound()
    got = plugin_api._fetch_inbound_for_reply_context(
        client, source_message_id="MSG1", thread_id="TH1",
    )
    assert got is client.get_message.return_value
    client.get_message.assert_called_once_with("MSG1")
    client.get_thread.assert_not_called()


def test_fetch_inbound_falls_back_to_thread_tail(cal_db):
    plugin_api = _load_plugin_api()
    GmailUnavailable = plugin_api.GmailUnavailable
    client = MagicMock()
    client.get_message.side_effect = [
        GmailUnavailable("not found"),
        _inbound(msg_id="TAIL"),
    ]
    client.get_thread.return_value = [{"id": "TAIL", "from": "kol@x.com", "date": "", "body": ""}]
    got = plugin_api._fetch_inbound_for_reply_context(
        client,
        source_message_id="proactive-followup:TEST:1:999",
        thread_id="TH99",
    )
    assert got.message_id == "TAIL"
    client.get_thread.assert_called_once_with("TH99")
    assert client.get_message.call_args_list[-1][0][0] == "TAIL"


def test_create_draft_proactive_uses_thread_for_cc_and_quote(cal_db):
    plugin_api = _load_plugin_api()
    GmailUnavailable = plugin_api.GmailUnavailable
    inbound = _inbound()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "us@brand.com"
    mock_client.get_thread.return_value = [{"id": "MSG1", "from": "", "date": "", "body": ""}]
    mock_client.get_message.side_effect = [GmailUnavailable("synthetic id"), inbound]
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D1", message_id="MNEW", thread_id="TH1",
    )

    approval_value = {
        "source_message_id": "proactive-followup:TEST:42:1700000000",
        "draft": {
            "to": "kol@example.com",
            "subject": "Re: collab",
            "body": "Hi — just checking in on timing.",
            "thread_id": "TH1",
        },
    }

    with patch.object(plugin_api, "GmailClient", return_value=mock_client), patch.object(
        plugin_api,
        "_resolve_thread_id_from_events",
        return_value="TH1",
    ):
        out = plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=42,
            campaign_id="C1",
            approval_value=approval_value,
            env="TEST",
        )

    assert out["draft_id"] == "D1"
    kwargs = mock_client.create_draft.call_args.kwargs
    assert "manager@agency.com" in (kwargs.get("cc") or "")
    assert "Prior mail body here." in kwargs["body"]
    assert "On Mon, 1 Jan 2024" in kwargs["body"]
    assert kwargs["body"].startswith("Hi — just checking in")


def test_create_draft_skips_duplicate_quote_when_body_has_marker(cal_db):
    plugin_api = _load_plugin_api()
    inbound = _inbound()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "us@brand.com"
    mock_client.get_message.return_value = inbound
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D2", message_id="M2", thread_id="TH1",
    )
    body_with_quote = (
        "Thanks!\n\nOn Mon, 1 Jan 2024 12:00:00 +0000, kol@example.com wrote:\n"
        "> Prior mail body here."
    )
    approval_value = {
        "source_message_id": "MSG1",
        "draft": {
            "to": "kol@example.com",
            "subject": "Re: collab",
            "body": body_with_quote,
            "thread_id": "TH1",
        },
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client), patch.object(
        plugin_api,
        "_resolve_thread_id_from_events",
        return_value="TH1",
    ):
        plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=1,
            campaign_id="C1",
            approval_value=approval_value,
            env="TEST",
        )
    body_sent = mock_client.create_draft.call_args.kwargs["body"]
    assert body_sent.count("Prior mail body here.") == 1


def test_self_emails_include_env_extra(cal_db):
    plugin_api = _load_plugin_api()
    client = MagicMock()
    client.get_profile_email.return_value = "primary@brand.com"
    with patch.dict("os.environ", {"KOL_OPS_GMAIL_REPLY_SELF": "ops@brand.com, Primary@Brand.com"}):
        emails = plugin_api._gmail_self_emails(client)
    assert emails == {"primary@brand.com", "ops@brand.com"}
