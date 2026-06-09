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


def test_append_pending_inbound_first_is_trigger():
    ei = _load_escalation_inbounds()
    anchor = ei.inbound_anchor_from_payload({"message_id": "MSG1", "snippet": "Hi"})
    out = ei.append_pending_inbound({}, anchor)
    assert out["pending_inbounds"][0]["role"] == "trigger"


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


def test_select_escalation_ids_prefers_inbound_tagged():
    ei = _load_escalation_inbounds()
    rows = [
        {
            "id": 10,
            "resume_context_json": '{"source": "discovery_floor"}',
        },
        {
            "id": 20,
            "resume_context_json": (
                '{"source": "classifier", "thread_id": "TH1", '
                '"source_message_id": "MSG1"}'
            ),
        },
    ]
    anchor = {"message_id": "MSG2", "thread_id": "TH1"}
    ids = ei.select_escalation_ids_for_followup(
        rows,
        anchor,
        parse_ctx=lambda raw: __import__("json").loads(raw or "{}"),
    )
    assert ids == [20]


def test_append_targets_single_escalation_when_multiple_open(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@kmulti", platform="instagram")
    cid = "C-MULTI"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="outreach",
        reason="discovery_floor",
        env="TEST",
        resume_context={"source": "internal"},
    )
    esc_inbound = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="variant_swap",
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
            "snippet": "follow up",
        },
    )
    assert n == 1
    inbound_row = cal_db.get_escalation(esc_inbound)
    all_open = cal_db.list_escalations(
        state="awaiting_answer", env="TEST", identity_id=iid, campaign_id=cid,
    )
    internal_row = next(r for r in all_open if r["id"] != esc_inbound)
    internal_ctx = internal_row.get("resume_context") or {}
    assert not internal_ctx.get("pending_inbounds")
    ctx = inbound_row.get("resume_context") or {}
    assert "MSG2" in [x.get("message_id") for x in ctx.get("pending_inbounds") or []]


def test_first_inbound_without_seed_is_trigger_no_question_append(cal_db):
    """Legacy open without source_message_id: first event append uses trigger role."""
    iid = cal_db.upsert_identity(primary_handle="@klegacy", platform="instagram")
    cid = "C-LEG"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
        question_to_operator="请确认预算上限？",
        resume_context={"source": "classifier", "thread_id": "TH1"},
    )
    row0 = cal_db.get_escalation(esc_id)
    ctx0 = row0.get("resume_context") or {}
    ctx0.pop("pending_inbounds", None)
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "UPDATE kol_escalations SET resume_context_json=? WHERE id=?",
            (__import__("json").dumps(ctx0), esc_id),
        )
    n = cal_db.append_pending_inbound_on_inbound_event(
        identity_id=iid,
        campaign_id=cid,
        env="TEST",
        payload={"message_id": "MSG1", "thread_id": "TH1", "snippet": "What budget?"},
    )
    assert n == 1
    row = cal_db.get_escalation(esc_id)
    pending = (row.get("resume_context") or {}).get("pending_inbounds") or []
    assert pending[0]["message_id"] == "MSG1"
    assert pending[0]["role"] == "trigger"
    assert row.get("question_to_operator") == "请确认预算上限？"


def test_sync_pending_inbounds_backfill(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@ksync", platform="instagram")
    cid = "C-SYNC"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG1", "thread_id": "TH1", "snippet": "trigger"},
    )
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
        resume_context={"source": "classifier", "source_message_id": "MSG1"},
    )
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG2", "thread_id": "TH1", "snippet": "follow"},
    )
    row = cal_db.get_escalation(esc_id)
    ctx = dict(row.get("resume_context") or {})
    ctx.pop("pending_inbounds", None)
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "UPDATE kol_escalations SET resume_context_json=? WHERE id=?",
            (__import__("json").dumps(ctx), esc_id),
        )
    out = cal_db.sync_escalation_pending_inbounds(esc_id)
    assert out.get("synced") is True
    row2 = cal_db.get_escalation(esc_id)
    mids = [x.get("message_id") for x in (row2.get("resume_context") or {}).get("pending_inbounds") or []]
    assert "MSG1" in mids and "MSG2" in mids


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
