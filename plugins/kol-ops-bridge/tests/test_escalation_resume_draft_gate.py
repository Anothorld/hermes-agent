"""Tests for inbound escalation resume_context enrichment."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(fq, _PLUGIN_ROOT / "plugin_api.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _open_mode(monkeypatch, cal_db):
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _provided: None)


def test_open_escalation_enriches_classifier_source_message_id(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@enrich", platform="instagram")
    cid = "C-ENRICH"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={
            "message_id": "MSG-INBOUND-1",
            "thread_id": "TH-ENRICH",
            "from_addr": "kol@agency.com",
            "subject": "Re: collab",
        },
    )
    out = plugin_api.open_escalation(
        body=plugin_api.EscalationOpenBody(
            identity_id=iid,
            campaign_id=cid,
            env="TEST",
            goal="compensation_negotiation",
            reason="variant_swap_and_scope_change",
            resume_context={"source": "classifier"},
        ),
        x_bridge_key=None,
    )
    esc_id = out["escalation_id"]
    row = cal_db.get_escalation(esc_id)
    ctx = row.get("resume_context") or {}
    assert ctx.get("source_message_id") == "MSG-INBOUND-1"
    assert ctx.get("thread_id") == "TH-ENRICH"


def test_open_escalation_preserves_explicit_source_message_id(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@explicit", platform="instagram")
    cid = "C-EXPL"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG-NEW", "thread_id": "TH2"},
    )
    out = plugin_api.open_escalation(
        body=plugin_api.EscalationOpenBody(
            identity_id=iid,
            campaign_id=cid,
            env="TEST",
            goal="compensation_negotiation",
            reason="test",
            resume_context={
                "source": "classifier",
                "source_message_id": "MSG-EXPLICIT",
            },
        ),
        x_bridge_key=None,
    )
    row = cal_db.get_escalation(out["escalation_id"])
    ctx = row.get("resume_context") or {}
    assert ctx.get("source_message_id") == "MSG-EXPLICIT"
