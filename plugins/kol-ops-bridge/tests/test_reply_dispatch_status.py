"""Tests for inbound reply poller idempotency (cal.reply_dispatch_status)."""

from __future__ import annotations


def test_reply_dispatch_status_empty(bridge_pkg, cal_db):
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="@t", primary_email="t@example.com", env="LIVE")
    cid = "C1"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    out = cal.reply_dispatch_status(
        identity_id=iid,
        campaign_id=cid,
        message_id="msg1",
        env="LIVE",
    )
    assert out["should_skip_poller"] is False
    assert out["should_retry_gateway_only"] is False


def test_reply_dispatch_status_skip_when_draft_ready(bridge_pkg, cal_db):
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="@t2", primary_email="t2@example.com", env="LIVE")
    cid = "C2"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    mid = "msg-draft"
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_reply_draft_ready",
        actor="test",
        payload={
            "source_message_id": mid,
            "child_skill": "kol-reply-synthesizer",
            "draft": {"body": "hi", "to": "a@b.com", "subject": "Re: x"},
        },
        env="LIVE",
    )
    out = cal.reply_dispatch_status(
        identity_id=iid, campaign_id=cid, message_id=mid, env="LIVE",
    )
    assert out["has_draft_ready_event"] is True
    assert out["should_skip_poller"] is True


def test_reply_dispatch_status_retry_gateway_only(bridge_pkg, cal_db):
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="@t3", primary_email="t3@example.com", env="LIVE")
    cid = "C3"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    mid = "msg-inbound-only"
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="cron",
        payload={"message_id": mid, "subject": "Re: hi"},
        env="LIVE",
    )
    out = cal.reply_dispatch_status(
        identity_id=iid, campaign_id=cid, message_id=mid, env="LIVE",
    )
    assert out["has_inbound_event"] is True
    assert out["should_retry_gateway_only"] is True
    assert out["should_skip_poller"] is False
