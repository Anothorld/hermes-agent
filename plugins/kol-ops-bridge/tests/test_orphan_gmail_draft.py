"""Tests for chase orphan Gmail draft discard."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_orphan_draft_id_from_approval_gmail_draft(bridge_pkg):
    ogd = bridge_pkg.orphan_gmail_draft
    draft_id = ogd.orphan_draft_id({
        "decision": "approved",
        "gmail_draft": {"draft_id": "DRAFT-OLD", "thread_id": "TH1"},
    })
    assert draft_id == "DRAFT-OLD"


def test_orphan_draft_id_falls_back_to_offer_fact(bridge_pkg):
    ogd = bridge_pkg.orphan_gmail_draft
    draft_id = ogd.orphan_draft_id(
        {"decision": "pending", "draft": {"body": "x"}},
        offer_facts={"offer.gmail_draft_id": "DRAFT-OFFER"},
    )
    assert draft_id == "DRAFT-OFFER"


def test_orphan_draft_id_none_when_missing(bridge_pkg):
    ogd = bridge_pkg.orphan_gmail_draft
    assert ogd.orphan_draft_id({"decision": "pending"}) is None


def test_discard_deletes_and_clears_offer_facts(cal_db, bridge_pkg, monkeypatch):
    ogd = bridge_pkg.orphan_gmail_draft
    iid = cal_db.upsert_identity(primary_handle="@og", platform="instagram")
    cid = "C-OG"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.write_facts(
        identity_id=iid,
        campaign_id=cid,
        namespace="offer",
        facts={
            "offer.gmail_draft_id": "DRAFT-OLD",
            "offer.gmail_thread_id": "TH1",
        },
        source="test",
        env="TEST",
    )
    client = MagicMock()
    client.delete_draft.return_value = {"status": "deleted", "draftId": "DRAFT-OLD"}

    out = ogd.discard_orphan_gmail_draft(
        identity_id=iid,
        campaign_id=cid,
        env="TEST",
        prior_fact={
            "decision": "approved",
            "gmail_draft": {"draft_id": "DRAFT-OLD", "thread_id": "TH1"},
        },
        client=client,
    )
    assert out["action"] == "deleted"
    client.delete_draft.assert_called_once_with(draft_id="DRAFT-OLD")
    latest = cal_db.latest_facts_for(identity_id=iid, campaign_id=cid, env="TEST")
    assert latest.get("offer.gmail_draft_id") == ""
    assert latest.get("offer.gmail_thread_id") == ""


def test_discard_skipped_without_draft_id(cal_db, bridge_pkg):
    ogd = bridge_pkg.orphan_gmail_draft
    iid = cal_db.upsert_identity(primary_handle="@og2", platform="instagram")
    cid = "C-OG2"
    out = ogd.discard_orphan_gmail_draft(
        identity_id=iid,
        campaign_id=cid,
        env="TEST",
        prior_fact={"decision": "pending", "draft": {"body": "x"}},
    )
    assert out["action"] == "skipped"
    assert out["reason"] == "no_orphan_draft_id"


def test_persist_supersedes_deletes_orphan_gmail_draft(cal_db, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    pytest.importorskip("fastapi")
    plugin_root = Path(__file__).resolve().parents[1]
    fq = "kol_ops_bridge_pkg.plugin_api"
    if fq not in sys.modules:
        spec = importlib.util.spec_from_file_location(fq, plugin_root / "plugin_api.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fq] = mod
        spec.loader.exec_module(mod)
    plugin_api = sys.modules[fq]
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _provided: None)

    deleted: list[str] = []

    class _FakeClient:
        def delete_draft(self, *, draft_id: str):
            deleted.append(draft_id)
            return {"status": "deleted", "draftId": draft_id}

    monkeypatch.setattr(
        plugin_api.orphan_gmail_draft,
        "resolve_campaign_gmail_client",
        lambda **kwargs: _FakeClient(),
    )

    iid = cal_db.upsert_identity(primary_handle="@k5", platform="instagram")
    cid = "C5"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.write_facts(
        identity_id=iid,
        campaign_id=cid,
        namespace="approval",
        facts={"approval.reply_draft": {
            "decision": "approved",
            "source_message_id": "MSG1",
            "primary_lane": "commerce",
            "primary_goal": "product_selection",
            "child_skill": "kol-reply-synthesizer",
            "gmail_draft": {"draft_id": "DRAFT-ORPHAN", "thread_id": "TH1"},
            "draft": {
                "subject": "Re: collab",
                "body": "Old draft body",
                "to": "manager@agency.com",
                "thread_id": "TH1",
            },
        }},
        source="draft:MSG1",
        env="TEST",
    )
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG1", "thread_id": "TH1", "from_addr": "a@b.com", "subject": "Re: x"},
    )
    out = plugin_api.persist_reply_draft(
        body=plugin_api.PersistReplyDraftBody(
            identity_id=iid,
            campaign_id=cid,
            env="TEST",
            source_message_id="MSG2",
            primary_lane="commerce",
            primary_goal="product_selection",
            child_skill="kol-reply-synthesizer",
            child_envelope={"body": "Thanks for following up!"},
            latest_email={
                "from": "a@b.com",
                "subject": "Re: x",
                "thread_id": "TH1",
            },
        ),
        x_bridge_key=None,
    )
    assert out["chase_superseded"] is True
    assert deleted == ["DRAFT-ORPHAN"]
    assert out["orphan_gmail_discard"]["action"] == "deleted"
