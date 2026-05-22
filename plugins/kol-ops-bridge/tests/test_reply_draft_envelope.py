"""Regression: when an ``approval.reply_draft`` fact arrives with a
sparse envelope (no ``to``, ``subject: null``) because some reply-side
child skill wrote a draft pre-Layer-B, the bridge must recover the
recipient and subject from the matching ``kol_inbound_reply`` event
instead of failing the operator's approve click.

Exercises ``_create_gmail_draft_for_reply_approval`` directly with a
``DraftResult``-returning fake ``GmailClient`` so no real Gmail call is
made. Pattern copied from ``test_approval_value_preserved.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402  (after importorskip)

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(
        fq, _PLUGIN_ROOT / "plugin_api.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass(frozen=True)
class _FakeDraftResult:
    draft_id: str
    message_id: str
    thread_id: str


class _FakeGmailClient:
    """Records create_draft kwargs and returns a deterministic result."""

    last_kwargs: dict[str, Any] = {}

    def is_available(self) -> bool:  # noqa: D401 — bridge contract
        return True

    def create_draft(self, **kwargs: Any) -> _FakeDraftResult:
        _FakeGmailClient.last_kwargs = dict(kwargs)
        return _FakeDraftResult(
            draft_id="DRAFT-1", message_id="MSG-1", thread_id="THREAD-1",
        )


def _seed_inbound(cal_db, *, identity_id: int, campaign_id: str,
                  message_id: str, thread_id: str, from_addr: str,
                  subject: str) -> None:
    cal_db.write_event(
        identity_id=identity_id,
        campaign_id=campaign_id,
        event_type="kol_inbound_reply",
        actor="poller:gmail",
        env="TEST",
        payload={
            "message_id": message_id,
            "thread_id": thread_id,
            "from_addr": from_addr,
            "subject": subject,
        },
    )


def test_envelope_resolved_from_inbound_event(cal_db, monkeypatch):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t1", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST",
                                  test_mode_to="t@x.com")
    _seed_inbound(
        cal_db, identity_id=iid, campaign_id="C1",
        message_id="M1", thread_id="TH1",
        from_addr="kol@x.com", subject="Re: budget",
    )
    monkeypatch.setattr(plugin_api, "GmailClient", _FakeGmailClient)

    approval_value = {
        "decision": "pending",
        "source_message_id": "M1",
        "child_skill": "kol-compensation-negotiator",
        "draft": {
            # sparse — subject/to missing, body+thread_id only
            "body": "Hi alice, the cap is $1500.",
            "thread_id": "TH1",
        },
    }
    out = plugin_api._create_gmail_draft_for_reply_approval(
        identity_id=iid, campaign_id="C1",
        approval_value=approval_value, env="TEST",
    )
    assert out["draft_id"] == "DRAFT-1"
    seen = _FakeGmailClient.last_kwargs
    assert seen["to"] == "kol@x.com"
    assert seen["subject"] == "Re: budget"
    assert seen["body"] == "Hi alice, the cap is $1500."


def test_envelope_resolved_subject_prefixed_when_missing_re(cal_db, monkeypatch):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t2", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C2", env="TEST",
                                  test_mode_to="t@x.com")
    _seed_inbound(
        cal_db, identity_id=iid, campaign_id="C2",
        message_id="M2", thread_id="TH2",
        from_addr="kol@y.com", subject="budget",  # no Re: prefix
    )
    monkeypatch.setattr(plugin_api, "GmailClient", _FakeGmailClient)

    approval_value = {
        "decision": "pending",
        "source_message_id": "M2",
        "draft": {"body": "ok.", "thread_id": "TH2"},
    }
    plugin_api._create_gmail_draft_for_reply_approval(
        identity_id=iid, campaign_id="C2",
        approval_value=approval_value, env="TEST",
    )
    assert _FakeGmailClient.last_kwargs["subject"] == "Re: budget"


def test_no_inbound_event_returns_400_with_named_fields(cal_db, monkeypatch):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t3", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C3", env="TEST",
                                  test_mode_to="t@x.com")
    # No kol_inbound_reply event seeded — resolver returns (None, None).
    monkeypatch.setattr(plugin_api, "GmailClient", _FakeGmailClient)

    approval_value = {
        "decision": "pending",
        "source_message_id": "M3",
        "draft": {"body": "hi.", "thread_id": "TH3"},
    }
    with pytest.raises(HTTPException) as exc_info:
        plugin_api._create_gmail_draft_for_reply_approval(
            identity_id=iid, campaign_id="C3",
            approval_value=approval_value, env="TEST",
        )
    assert exc_info.value.status_code == 400
    detail = str(exc_info.value.detail)
    assert "subject" in detail
    assert "to" in detail
