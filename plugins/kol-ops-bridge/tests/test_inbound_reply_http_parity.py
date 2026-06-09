"""Http vs in-process adapter parity for read-only paths."""

from __future__ import annotations

from unittest.mock import MagicMock

from kol_ops_bridge_pkg.inbound_reply_ports.http import HttpBridgeAdapter
from kol_ops_bridge_pkg.inbound_reply_ports.in_process import InProcessBridgeAdapter


def test_get_facts_empty_when_no_identity(bridge_pkg, cal_db):
    adapter = InProcessBridgeAdapter()
    assert adapter.get_facts(identity_id=99999, campaign_id="C1", env="TEST") == {}


def test_in_process_get_facts_matches_cal(bridge_pkg, cal_db):
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="@parity", platform="instagram", env="TEST")
    assert iid is not None
    cal.write_facts(
        identity_id=int(iid),
        campaign_id="C1",
        namespace="offer",
        facts={"offer.gmail_mailbox_email": "ops@brand.com"},
        source="test",
        env="TEST",
    )
    adapter = InProcessBridgeAdapter()
    facts = adapter.get_facts(identity_id=int(iid), campaign_id="C1", env="TEST")
    direct = cal.latest_facts_for(identity_id=int(iid), campaign_id="C1", env="TEST")
    assert facts == direct
    assert facts.get("offer.gmail_mailbox_email") == "ops@brand.com"


def test_http_get_facts_unwraps_facts_envelope(bridge_pkg):
    adapter = HttpBridgeAdapter(base="http://127.0.0.1:9999", bridge_key="k")
    mock_client = MagicMock()
    mock_client.request.return_value = {
        "facts": {"offer.gmail_mailbox_email": "ops@brand.com"},
    }
    adapter._client = mock_client
    facts = adapter.get_facts(identity_id=1, campaign_id="C1", env="TEST")
    assert facts == {"offer.gmail_mailbox_email": "ops@brand.com"}
    mock_client.request.assert_called_once()
