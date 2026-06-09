"""In-process bridge adapter integration tests."""

from __future__ import annotations

from kol_ops_bridge_pkg.inbound_reply_ports.in_process import InProcessBridgeAdapter


def test_in_process_list_events_empty(bridge_pkg, cal_db):
    adapter = InProcessBridgeAdapter()
    events = adapter.list_recent_events(env="TEST", limit=10)
    assert events == []


def test_in_process_get_identity_missing(bridge_pkg, cal_db):
    adapter = InProcessBridgeAdapter()
    assert adapter.get_identity(999999) is None
