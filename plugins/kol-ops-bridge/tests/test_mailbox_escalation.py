"""Tests for mailbox mismatch auto-escalation."""

from __future__ import annotations


def test_ensure_mailbox_mismatch_idempotent(cal_db, bridge_pkg, monkeypatch):
    me = bridge_pkg.mailbox_escalation
    opened: list[dict] = []

    def _open(**kwargs):
        opened.append(kwargs)
        return len(opened)

    monkeypatch.setattr(me.cal, "list_escalations", lambda **_k: [])
    monkeypatch.setattr(me.cal, "open_escalation", _open)

    eid1 = me.ensure_mailbox_mismatch_escalation(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        message_id="MSG1",
        thread_id="TH1",
        bound_mailbox_email="alice@brand.com",
        detected_mailbox_email="bob@brand.com",
    )
    assert eid1 == 1
    assert opened[0]["reason"] == "inbound_mailbox_mismatch"

    monkeypatch.setattr(
        me.cal,
        "list_escalations",
        lambda **_k: [{
            "id": 99,
            "identity_id": 1,
            "campaign_id": "C1",
            "reason": "inbound_mailbox_mismatch",
            "resume_context": {"source_message_id": "MSG1"},
        }],
    )
    eid2 = me.ensure_mailbox_mismatch_escalation(
        identity_id=1,
        campaign_id="C1",
        env="TEST",
        message_id="MSG1",
        thread_id="TH1",
        bound_mailbox_email="alice@brand.com",
        detected_mailbox_email="bob@brand.com",
    )
    assert eid2 == 99
    assert len(opened) == 1


def test_reply_dispatch_status_skips_on_mailbox_mismatch_esc(cal_db, bridge_pkg):
    cal = bridge_pkg.cal
    iid = cal.upsert_identity(primary_handle="mismatch_kol", primary_email="k@ex.com")
    cal.open_escalation(
        identity_id=iid,
        campaign_id="C1",
        env="TEST",
        reason="inbound_mailbox_mismatch",
        resume_context={"source_message_id": "MSG99"},
    )
    out = cal.reply_dispatch_status(
        identity_id=iid,
        campaign_id="C1",
        message_id="MSG99",
        env="TEST",
    )
    assert out["has_mailbox_mismatch_escalation"] is True
    assert out["should_skip_poller"] is True
