"""Tests for automated inbound reply detection."""

from __future__ import annotations

from kol_ops_bridge_pkg.inbound_reply.automated import is_automated_inbound_reply_payload


def test_detects_mailer_daemon_bounce():
    payload = {
        "from_addr": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        "subject": "Delivery Status Notification (Failure)",
        "body": "** Address not found **\nYour message wasn't delivered",
    }
    assert is_automated_inbound_reply_payload(payload) is True


def test_detects_out_of_office_auto_reply():
    payload = {
        "from_addr": "Creator <creator@example.com>",
        "subject": "OUT OF OFFICE Re: Collab",
        "body": "Thanks for reaching out, I am out of office.",
    }
    assert is_automated_inbound_reply_payload(payload) is True


def test_allows_real_human_reply():
    payload = {
        "from_addr": "Creator <creator@example.com>",
        "subject": "Re: Collab idea",
        "body": "Thanks for reaching out — I'd love to hear more about deliverables.",
    }
    assert is_automated_inbound_reply_payload(payload) is False
