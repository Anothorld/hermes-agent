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
_THREAD = "19e81ff6def3b65f"
_MSG = "19e84b2d4cf91067"
_THREAD_OTHER = "19e81ff6def3b664a"


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


def _inbound(*, msg_id: str = _MSG, thread_id: str = _THREAD, body_html: str = ""):
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
        body_plain="Prior mail body here.",
        body_html=body_html,
    )


def _thread_exists(client: MagicMock, thread_id: str = _THREAD) -> None:
    """Approve-time draft creation verifies thread_id via get_thread."""
    client.get_thread.side_effect = lambda tid: (
        [{"id": _MSG, "from": "", "date": "", "body": ""}]
        if tid == thread_id
        else []
    )


def test_fetch_inbound_prefers_source_message_id(cal_db):
    plugin_api = _load_plugin_api()
    client = MagicMock()
    client.get_message.return_value = _inbound()
    got = plugin_api._fetch_inbound_for_reply_context(
        client, source_message_id=_MSG, thread_id=_THREAD,
    )
    assert got is client.get_message.return_value
    client.get_message.assert_called_once_with(_MSG)
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
        thread_id=_THREAD_OTHER,
    )
    assert got.message_id == "TAIL"
    client.get_thread.assert_called_once_with(_THREAD_OTHER)
    assert client.get_message.call_args_list[-1][0][0] == "TAIL"


def test_create_draft_proactive_uses_thread_for_cc_and_quote(cal_db):
    plugin_api = _load_plugin_api()
    GmailUnavailable = plugin_api.GmailUnavailable
    inbound = _inbound()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "us@brand.com"
    _thread_exists(mock_client, thread_id=_THREAD)
    mock_client.get_message.side_effect = [GmailUnavailable("synthetic id"), inbound]
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D1", message_id="MNEW", thread_id=_THREAD,
    )

    approval_value = {
        "source_message_id": "proactive-followup:TEST:42:1700000000",
        "draft": {
            "to": "kol@example.com",
            "subject": "Re: collab",
            "body": "Hi — just checking in on timing.",
            "thread_id": _THREAD,
        },
    }

    with patch.object(plugin_api, "GmailClient", return_value=mock_client), patch.object(
        plugin_api,
        "_resolve_thread_id_from_events",
        return_value=_THREAD,
    ):
        out = plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=42,
            campaign_id="C1",
            approval_value=approval_value,
            env="TEST",
        )

    assert out["draft_id"] == "D1"
    kwargs = mock_client.create_draft.call_args.kwargs
    assert kwargs.get("html") is True
    assert "manager@agency.com" in (kwargs.get("cc") or "")
    assert "Prior mail body here." in kwargs["body"]
    assert "gmail_extra" in kwargs["body"]
    assert "Hi — just checking in" in kwargs["body"]
    assert kwargs.get("reply_to_message_id") == _MSG


def test_create_draft_techjoyce_top_level_thread_anchors(cal_db):
    """Regression: synthesizer wrote thread_id/in_reply_to at fact top-level."""
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@techjoyce", platform="instagram")
    cid = "SEB8008-20260525"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={
            "message_id": "19e84b2d4cf91067",
            "thread_id": "19e81ff6def3b65f",
            "from_addr": "Ankush Bhasin <ankush@sparkmedia.la>",
            "subject": "Re: POVISON x @techjoyce — Smart Sofa Bed Collab",
        },
    )
    inbound = SimpleNamespace(
        message_id="19e84b2d4cf91067",
        thread_id="19e81ff6def3b65f",
        from_addr="Ankush Bhasin <ankush@sparkmedia.la>",
        to="candice@povison-collab.com",
        cc="",
        subject="Re: POVISON x @techjoyce — Smart Sofa Bed Collab",
        snippet="",
        in_reply_to=None,
        references=None,
        date="Mon, 1 Jun 2026 15:39:31 -0400",
        body="Can you share more details?",
    )
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "candice@povison-collab.com"
    mock_client.get_message.return_value = inbound
    mock_client.get_thread.side_effect = lambda tid: (
        [{"id": "19e84b2d4cf91067", "from": "", "date": "", "body": ""}]
        if tid == "19e81ff6def3b65f"
        else []
    )
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D-TJ",
        message_id="M-TJ",
        thread_id="19e81ff6def3b65f",
    )
    approval_value = {
        "contributing_skills": [
            {"goal": "product_selection", "lane": "commerce", "skill": "kol-product-selector"},
        ],
        "draft": {
            "body": "Hi Ankush,\n\nThanks for getting back to us!",
            "subject": "Re: POVISON x @techjoyce — Smart Sofa Bed Collab",
            "to": "Ankush Bhasin <ankush@sparkmedia.la>",
        },
        "event_id": 4565,
        "in_reply_to": "19e84b2d4cf91067",
        "primary_goal": "product_selection",
        "primary_lane": "commerce",
        "thread_id": "19e81ff6def3b65f",
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client):
        out = plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=iid,
            campaign_id=cid,
            approval_value=approval_value,
            env="TEST",
        )
    assert out["thread_id"] == "19e81ff6def3b65f"
    kwargs = mock_client.create_draft.call_args.kwargs
    assert kwargs["thread_id"] == "19e81ff6def3b65f"
    mock_client.get_message.assert_called_once_with("19e84b2d4cf91067")


def test_create_draft_quotes_parent_message_html_with_nested_history(cal_db):
    """Gmail web Reply embeds the parent MIME body (incl. nested quotes)."""
    plugin_api = _load_plugin_api()
    chase_msg = "19e84b2d4cf91070"
    parent_html = (
        '<div dir="ltr">Unfortunately we will pass.</div>'
        '<div class="gmail_quote"><blockquote type="cite">'
        "Here is our offer."
        "</blockquote></div>"
        '<div class="gmail_quote"><blockquote type="cite">'
        "nested older text"
        "</blockquote></div>"
    )
    inbound = SimpleNamespace(
        message_id=chase_msg,
        thread_id=_THREAD,
        from_addr="Shay <slevene@viralnation.com>",
        to="candice@povison-collab.com",
        cc="",
        subject="Re: collab",
        snippet="",
        in_reply_to=None,
        references=None,
        date="Thu, 4 Jun 2026 04:12:45 -0400",
        body="Unfortunately we will pass.",
        body_plain="Unfortunately we will pass.",
        body_html=parent_html,
    )
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "candice@povison-collab.com"
    mock_client.get_message.return_value = inbound
    _thread_exists(mock_client, thread_id=_THREAD)
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D3", message_id="M3", thread_id=_THREAD,
    )
    approval_value = {
        "source_message_id": chase_msg,
        "draft": {
            "to": "slevene@viralnation.com",
            "subject": "Re: collab",
            "body": "Hi Shay,\n\nTotally understand.",
            "thread_id": _THREAD,
        },
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client), patch.object(
        plugin_api,
        "_resolve_thread_id_from_events",
        return_value=_THREAD,
    ):
        plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=1,
            campaign_id="C1",
            approval_value=approval_value,
            env="TEST",
        )
    kwargs = mock_client.create_draft.call_args.kwargs
    body_sent = kwargs["body"]
    assert "Unfortunately we will pass." in body_sent
    assert "Here is our offer." in body_sent
    assert "nested older text" in body_sent
    assert "gmail_extra" in body_sent
    assert kwargs.get("html") is True
    assert kwargs.get("reply_to_message_id") == chase_msg


def test_create_draft_skips_duplicate_quote_when_body_has_marker(cal_db):
    plugin_api = _load_plugin_api()
    inbound = _inbound()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "us@brand.com"
    _thread_exists(mock_client, thread_id=_THREAD)
    mock_client.get_message.return_value = inbound
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D2", message_id="M2", thread_id=_THREAD,
    )
    body_with_quote = (
        '<div dir="ltr">Thanks!</div>'
        '<div class="gmail_extra"><br><div class="gmail_quote">'
        '<div class="gmail_attr">On Mon, 1 Jan 2024 wrote:<br></div>'
        "<blockquote>Prior mail body here.</blockquote></div><br></div>"
    )
    approval_value = {
        "source_message_id": _MSG,
        "draft": {
            "to": "kol@example.com",
            "subject": "Re: collab",
            "body": body_with_quote,
            "thread_id": _THREAD,
        },
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client), patch.object(
        plugin_api,
        "_resolve_thread_id_from_events",
        return_value=_THREAD,
    ):
        plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=1,
            campaign_id="C1",
            approval_value=approval_value,
            env="TEST",
        )
    body_sent = mock_client.create_draft.call_args.kwargs["body"]
    assert body_sent.count("Prior mail body here.") == 1


def test_create_draft_initial_outreach_omits_gmail_thread(cal_db):
    """Cold outreach uses synthetic outreach_* anchors — Gmail draft is standalone."""
    plugin_api = _load_plugin_api()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D-COLD",
        message_id="M-COLD",
        thread_id="NEW-THREAD",
    )
    approval_value = {
        "primary_goal": "outreach",
        "child_skill": "kol-cold-outreach",
        "source_message_id": "draft:outreach_C1_99",
        "draft": {
            "to": "Techsource@theblendedgroup.com",
            "subject": "POVISON x @ed.techsource — TV Stand Collab",
            "body": "Hi Edgar,\n\nWould you be open to chatting?",
            "thread_id": "outreach_C1_99",
        },
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client):
        out = plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=99,
            campaign_id="C1",
            approval_value=approval_value,
            env="TEST",
        )
    assert out["draft_id"] == "D-COLD"
    kwargs = mock_client.create_draft.call_args.kwargs
    assert kwargs.get("thread_id") is None
    mock_client.get_thread.assert_not_called()


def test_create_draft_proactive_resolves_thread_from_facts(cal_db):
    """Synthetic thread_id in draft → fall back to offer.gmail_sent_thread_id."""
    plugin_api = _load_plugin_api()
    cal = plugin_api.cal
    iid = cal.upsert_identity(primary_handle="@nudge", platform="instagram")
    cid = "C1"
    cal.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal.write_facts(
        identity_id=iid,
        campaign_id=cid,
        namespace="offer",
        facts={"offer.gmail_sent_thread_id": _THREAD},
        source="test",
        env="TEST",
    )
    inbound = _inbound()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_profile_email.return_value = "us@brand.com"
    _thread_exists(mock_client, thread_id=_THREAD)
    mock_client.get_message.side_effect = [inbound]
    mock_client.create_draft.return_value = SimpleNamespace(
        draft_id="D-FACT",
        message_id="MNEW",
        thread_id=_THREAD,
    )
    approval_value = {
        "primary_goal": "proactive_followup",
        "child_skill": "kol-proactive-followup",
        "source_message_id": "proactive-followup:TEST:42:1700000000",
        "draft": {
            "to": "kol@example.com",
            "subject": "Re: collab",
            "body": "Follow up",
            "thread_id": "proactive-followup:TEST:42:1700000000",
            "kind": "proactive_followup",
        },
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client):
        out = plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=iid,
            campaign_id=cid,
            approval_value=approval_value,
            env="TEST",
        )
    assert out["draft_id"] == "D-FACT"
    kwargs = mock_client.create_draft.call_args.kwargs
    assert kwargs.get("thread_id") == _THREAD
    assert kwargs.get("reply_to_message_id") == _MSG


def test_create_draft_rejects_unresolvable_thread(cal_db):
    """Synthetic thread_id must not reach Gmail drafts.create."""
    from fastapi import HTTPException

    plugin_api = _load_plugin_api()
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.get_thread.return_value = []
    approval_value = {
        "source_message_id": "proactive-followup:TEST:42:1700000000",
        "draft": {
            "to": "kol@example.com",
            "subject": "Re: collab",
            "body": "Follow up",
            "thread_id": "proactive-followup:TEST:42:1700000000",
        },
    }
    with patch.object(plugin_api, "GmailClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            plugin_api._create_gmail_draft_for_reply_approval(
                identity_id=1,
                campaign_id="C1",
                approval_value=approval_value,
                env="TEST",
            )
    assert exc_info.value.status_code == 400
    assert "thread" in str(exc_info.value.detail).lower()
    mock_client.create_draft.assert_not_called()


def test_self_emails_include_env_extra(cal_db):
    plugin_api = _load_plugin_api()
    client = MagicMock()
    client.get_profile_email.return_value = "primary@brand.com"
    with patch.dict("os.environ", {"KOL_OPS_GMAIL_REPLY_SELF": "ops@brand.com, Primary@Brand.com"}):
        emails = plugin_api._gmail_self_emails(client)
    assert emails == {"primary@brand.com", "ops@brand.com"}
