"""Tests for escalation pending_inbounds anchors."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_escalation_inbounds():
    fq = "kol_ops_bridge_escalation_inbounds"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(
        fq, _PLUGIN_ROOT / "escalation_inbounds.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def test_seed_trigger_inbound():
    ei = _load_escalation_inbounds()
    ctx = ei.seed_trigger_inbound({
        "source": "classifier",
        "source_message_id": "MSG1",
        "thread_id": "TH1",
    })
    assert len(ctx["pending_inbounds"]) == 1
    assert ctx["pending_inbounds"][0]["message_id"] == "MSG1"
    assert ctx["pending_inbounds"][0]["role"] == "trigger"
    assert ctx["latest_pending_inbound_message_id"] == "MSG1"


def test_append_pending_inbound_dedup():
    ei = _load_escalation_inbounds()
    ctx = {"pending_inbounds": [{"message_id": "MSG1", "role": "trigger"}]}
    anchor = ei.inbound_anchor_from_payload(
        {"message_id": "MSG2", "snippet": "Any update?", "from_addr": "k@x.com"},
        role="followup",
    )
    out = ei.append_pending_inbound(ctx, anchor)
    assert len(out["pending_inbounds"]) == 2
    assert out["latest_pending_inbound_message_id"] == "MSG2"
    again = ei.append_pending_inbound(out, anchor)
    assert len(again["pending_inbounds"]) == 2


def test_append_on_inbound_event_integration(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@kinb", platform="instagram")
    cid = "C-INB"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
        resume_context={
            "source": "classifier",
            "source_message_id": "MSG1",
            "thread_id": "TH1",
        },
    )
    n = cal_db.append_pending_inbound_on_inbound_event(
        identity_id=iid,
        campaign_id=cid,
        env="TEST",
        payload={
            "message_id": "MSG2",
            "thread_id": "TH1",
            "snippet": "Following up — can you confirm budget?",
            "from_addr": "k@agency.com",
            "subject": "Re: collab",
        },
        event_id=99,
    )
    assert n == 1
    row = cal_db.get_escalation(esc_id)
    ctx = row.get("resume_context") or {}
    mids = [x.get("message_id") for x in ctx.get("pending_inbounds") or []]
    assert mids == ["MSG1", "MSG2"]
    assert ctx.get("latest_pending_inbound_message_id") == "MSG2"
    q = row.get("question_to_operator") or ""
    assert "【KOL 追信 · MSG2】" in q
    assert "Following up" in q
    assert "Re: collab" in q


def test_append_followup_to_suggested_question_dedup():
    ei = _load_escalation_inbounds()
    anchor = ei.inbound_anchor_from_payload({
        "message_id": "MSG2",
        "subject": "Re: x",
        "snippet": "Ping?",
    })
    once = ei.append_followup_to_suggested_question("原始问题？", anchor)
    twice = ei.append_followup_to_suggested_question(once, anchor)
    assert once == twice
    assert once.count("【KOL 追信") == 1
    assert "原始问题？" in once
    assert "Ping?" in once
